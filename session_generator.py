import asyncio
import os
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

# Load environment variables
load_dotenv()

async def create_string_session():
    """Create and save a Telegram string session"""
    
    # Get credentials from environment
    api_id = os.getenv("API_ID")
    api_hash = os.getenv("API_HASH")
    phone_number = os.getenv("PHONE_NUMBER")
    
    if not all([api_id, api_hash, phone_number]):
        print("❌ Error: Missing required environment variables!")
        print("Make sure you have API_ID, API_HASH, and PHONE_NUMBER in your .env file")
        return None
    
    print("🔄 Creating Telegram string session...")
    print(f"📱 Phone number: {phone_number}")
    
    # Create client with empty string session
    client = TelegramClient(StringSession(), int(api_id), api_hash)
    
    try:
        # Connect to Telegram
        await client.connect()
        
        # Check if already authorized
        if not await client.is_user_authorized():
            print("📨 Sending verification code...")
            await client.send_code_request(phone_number)
            
            # Get verification code from user
            code = input("🔢 Enter the verification code you received: ").strip()
            
            try:
                await client.sign_in(phone_number, code)
            except Exception as e:
                if "Two-step verification" in str(e) or "password" in str(e).lower():
                    password = input("🔐 Enter your 2FA password: ").strip()
                    await client.sign_in(password=password)
                else:
                    raise e
        
        # Get the string session
        session_string = client.session.save()
        
        print("✅ Successfully created string session!")
        print(f"📝 Session string length: {len(session_string)} characters")
        
        # Save to file
        with open("telegram_session.txt", "w") as f:
            f.write(session_string)
        
        print("💾 Session saved to 'telegram_session.txt'")
        
        # Also save to .env file
        try:
            with open(".env", "a") as f:
                f.write(f"\nTELEGRAM_SESSION_STRING={session_string}\n")
            print("💾 Session also added to .env file as TELEGRAM_SESSION_STRING")
        except Exception as e:
            print(f"⚠️  Could not save to .env file: {e}")
        
        # Test the session
        me = await client.get_me()
        print(f"🎉 Session test successful! Logged in as: {me.first_name} (@{me.username})")
        
        return session_string
        
    except Exception as e:
        print(f"❌ Error creating session: {e}")
        return None
    
    finally:
        await client.disconnect()

def save_session_to_env(session_string: str):
    """Add session string to environment variables"""
    env_content = ""
    
    # Read existing .env file
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            env_content = f.read()
    
    # Check if session string already exists
    if "TELEGRAM_SESSION_STRING" not in env_content:
        with open(".env", "a") as f:
            f.write(f"\nTELEGRAM_SESSION_STRING={session_string}\n")
        print("✅ Session string added to .env file")
    else:
        print("ℹ️  Session string already exists in .env file")

if __name__ == "__main__":
    print("🚀 Telegram String Session Generator")
    print("=" * 50)
    
    # Run the async function
    session = asyncio.run(create_string_session())
    
    if session:
        print("\n" + "=" * 50)
        print("📋 SETUP COMPLETE!")
        print("=" * 50)
        print("Your string session has been created and saved.")
        print("\n📌 Next steps:")
        print("1. Keep 'telegram_session.txt' file secure (contains your login session)")
        print("2. Add TELEGRAM_SESSION_STRING to your .env file if not done automatically")
        print("3. Run your FastAPI application - it will use the string session")
        print("\n⚠️  SECURITY NOTE:")
        print("- Never share your session string with anyone")
        print("- Don't commit telegram_session.txt to version control")
        print("- Add telegram_session.txt to your .gitignore file")
    else:
        print("\n❌ Failed to create session. Please check your credentials and try again.")