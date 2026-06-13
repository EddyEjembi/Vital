from db.database import initialize_database
from core.weather import fetch_weather
from core.notifications import send_notification
from scripts.test_presence_local import main as test_presence_main
from scripts.test_tts_local import main as test_tts_main

MESSAGE = "Hey Stan, you've been at your desk for 1 minute. It's time to take a break and stretch!"
initialize_database()
print(fetch_weather("Warri"))
send_notification("Vitál", MESSAGE)
test_tts_main(MESSAGE)
test_presence_main()