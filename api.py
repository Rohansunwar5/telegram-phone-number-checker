from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import os
import sqlite3
import time
from typing import List, Union
from dotenv import load_dotenv
from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession
import logging
import threading
from contextlib import asynccontextmanager

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global client and lock
client = None
client_lock = asyncio.Lock()
session_lock = threading.Lock()

class SafeTelegramClient:
    def __init__(self):
        self.client = None
        self.session_string = None
        self.last_used = 0
        self.connection_timeout = 300  # 5 minutes
        self._load_session_string()
        
    def _load_session_string(self):
        """Load session string from environment or file"""
        # Try to get from environment first
        self.session_string = os.getenv("TELEGRAM_SESSION_STRING")
        
        # If not in env, try to read from file
        if not self.session_string and os.path.exists("telegram_session.txt"):
            try:
                with open("telegram_session.txt", "r") as f:
                    self.session_string = f.read().strip()
                logger.info("✅ Loaded session string from telegram_session.txt")
            except Exception as e:
                logger.error(f"Failed to read session file: {e}")
        
        if self.session_string:
            logger.info("✅ Session string loaded successfully")
        else:
            logger.warning("⚠️  No session string found. Run session generator first!")
    
    async def get_client(self):
        async with client_lock:
            current_time = time.time()
            
            # Check if client needs refresh due to timeout or disconnection
            if (self.client is None or 
                not self.client.is_connected() or 
                current_time - self.last_used > self.connection_timeout):
                
                if self.client:
                    try:
                        await self.client.disconnect()
                    except:
                        pass
                
                # Check if we have a session string
                if not self.session_string:
                    raise HTTPException(
                        status_code=503, 
                        detail="No Telegram session found. Please run the session generator first."
                    )
                
                # Create new client with string session to avoid file locks
                session = StringSession(self.session_string)
                
                self.client = TelegramClient(
                    session,
                    int(os.getenv("API_ID")),
                    os.getenv("API_HASH")
                )
                
                try:
                    await self.client.connect()
                    
                    if not await self.client.is_user_authorized():
                        raise HTTPException(
                            status_code=401, 
                            detail="Telegram session expired. Please regenerate session string."
                        )
                        
                except Exception as e:
                    logger.error(f"Failed to connect to Telegram: {str(e)}")
                    if self.client:
                        try:
                            await self.client.disconnect()
                        except:
                            pass
                        self.client = None
                    raise HTTPException(status_code=503, detail=f"Failed to connect to Telegram: {str(e)}")
            
            self.last_used = current_time
            return self.client

# Global client manager
telegram_manager = SafeTelegramClient()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up Telegram Phone Checker API")
    yield
    # Shutdown
    logger.info("Shutting down...")
    if telegram_manager.client:
        try:
            await telegram_manager.client.disconnect()
        except:
            pass

app = FastAPI(lifespan=lifespan)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def check_single_number(client, number: str, semaphore: asyncio.Semaphore):
    """Check a single phone number with rate limiting"""
    async with semaphore:
        try:
            # Validate phone number format
            if not isinstance(number, str) or not number.startswith('+'):
                return number, {"error": "Invalid phone number format. Must start with '+'"}

            # Add small delay to prevent rate limiting
            await asyncio.sleep(0.1)
            
            # Use a more reliable method to check if user exists
            try:
                # Try to resolve the phone number
                result = await client(functions.contacts.ResolvePhoneRequest(phone=number))
                
                if result.users:
                    user = result.users[0]
                    return number, {
                        "id": user.id,
                        "username": getattr(user, 'username', None),
                        "usernames": getattr(user, 'usernames', None),
                        "first_name": getattr(user, 'first_name', None),
                        "last_name": getattr(user, 'last_name', None),
                        "phone": getattr(user, 'phone', None),
                        "verified": getattr(user, 'verified', False),
                        "premium": getattr(user, 'premium', False),
                        "status": "online" if isinstance(getattr(user, 'status', None), types.UserStatusOnline) else "offline",
                        "exists": True
                    }
                else:
                    return number, {"exists": False, "error": "User not found"}
                    
            except Exception as resolve_error:
                # Fallback to import contacts method
                logger.warning(f"ResolvePhone failed for {number}, trying ImportContacts: {str(resolve_error)}")
                
                contact = await client(functions.contacts.ImportContactsRequest([
                    types.InputPhoneContact(
                        client_id=hash(number) % 2147483647,  # Generate unique client_id
                        phone=number,
                        first_name="temp",
                        last_name=""
                    )
                ]))

                if contact.users:
                    user = contact.users[0]
                    result_data = {
                        "id": user.id,
                        "username": getattr(user, 'username', None),
                        "usernames": getattr(user, 'usernames', None),
                        "first_name": getattr(user, 'first_name', None),
                        "last_name": getattr(user, 'last_name', None),
                        "phone": getattr(user, 'phone', None),
                        "verified": getattr(user, 'verified', False),
                        "premium": getattr(user, 'premium', False),
                        "status": "online" if isinstance(getattr(user, 'status', None), types.UserStatusOnline) else "offline",
                        "exists": True
                    }
                    
                    # Clean up: Remove the contact
                    try:
                        await client(functions.contacts.DeleteContactsRequest(id=[user.id]))
                    except:
                        pass  # Ignore cleanup errors
                    
                    return number, result_data
                else:
                    return number, {"exists": False, "error": "User not found"}
                    
        except Exception as e:
            error_msg = str(e).lower()
            if "database is locked" in error_msg:
                logger.error(f"Database lock error for {number}: {str(e)}")
                # Wait and retry once
                await asyncio.sleep(1)
                try:
                    # Simple retry with basic check
                    contact = await client(functions.contacts.ImportContactsRequest([
                        types.InputPhoneContact(
                            client_id=hash(number + str(time.time())) % 2147483647,
                            phone=number,
                            first_name="temp",
                            last_name=""
                        )
                    ]))
                    
                    if contact.users:
                        user = contact.users[0]
                        try:
                            await client(functions.contacts.DeleteContactsRequest(id=[user.id]))
                        except:
                            pass
                        return number, {"exists": True, "id": user.id, "retried": True}
                    else:
                        return number, {"exists": False, "retried": True}
                except:
                    return number, {"error": "Database locked - please try again later"}
            
            logger.error(f"Error checking number {number}: {str(e)}")
            return number, {"error": str(e)}

@app.post("/check")
async def check_phone_number(phone_numbers: Union[List[str], dict] = Body(...)):
    try:
        # Handle both raw list and wrapped object formats
        if isinstance(phone_numbers, dict):
            if 'phone_numbers' in phone_numbers:
                numbers = phone_numbers['phone_numbers']
            elif 'phone_number' in phone_numbers:
                numbers = [phone_numbers['phone_number']]
            else:
                raise HTTPException(status_code=422, detail="Invalid request format. Use either list of numbers or {'phone_numbers': [...]}")
        else:
            numbers = phone_numbers

        if not isinstance(numbers, list):
            numbers = [numbers]

        # Limit batch size to prevent overwhelming the API
        if len(numbers) > 50:
            raise HTTPException(status_code=400, detail="Maximum 50 phone numbers per request")

        client = await telegram_manager.get_client()
        
        # Use semaphore to limit concurrent requests and prevent database locks
        semaphore = asyncio.Semaphore(3)  # Max 3 concurrent requests
        
        # Process all numbers concurrently but with rate limiting
        tasks = [check_single_number(client, number, semaphore) for number in numbers]
        completed_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        results = {}
        for result in completed_results:
            if isinstance(result, Exception):
                logger.error(f"Task failed with exception: {str(result)}")
                results["unknown"] = {"error": str(result)}
            else:
                number, data = result
                results[number] = data

        return JSONResponse(results, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "*"
        })
        
    except Exception as e:
        logger.error(f"Server error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        client = await telegram_manager.get_client()
        is_connected = client.is_connected() if client else False
        return {"status": "healthy", "telegram_connected": is_connected}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)