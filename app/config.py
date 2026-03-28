import os

import httpx
from dotenv import load_dotenv
from notion_client import Client

load_dotenv()

GOOGLE_KEY = os.getenv('GOOGLE_PLACES_API_KEY', '')
DB_ID = os.environ['NOTION_DATABASE_ID']
NOTION_TARGET_NAME = os.getenv('NOTION_TARGET_NAME', '行程安排')
HTTP_TIMEOUT = httpx.Timeout(15.0)
AZURE_OPENAI_ENDPOINT = os.getenv('AZURE_OPENAI_ENDPOINT', '')
AZURE_OPENAI_API_KEY = os.getenv('AZURE_OPENAI_API_KEY', '')
GOOGLE_PLACES_FIELD_MASK = (
    'places.id,'
    'places.displayName,'
    'places.formattedAddress,'
    'places.location,'
    'places.rating,'
    'places.googleMapsUri,'
    'places.regularOpeningHours.weekdayDescriptions,'
    'places.photos.name,'
    'places.reviews'
)
REGION_DAY_MAP = {
    '濟州市': 'Day1',
    '西部': 'Day2',
    '西歸浦': 'Day3',
    '東部': 'Day4',
}
REVIEW_SUMMARY_CATEGORIES = [
    '餐點',
    '服務',
    '環境',
    '排隊',
    '停車',
    '價格',
    '適合族群',
    '雷點',
]
REVIEW_LIMIT = 50
ARTICLE_LIMIT = 5

notion = Client(auth=os.environ['NOTION_TOKEN'])
