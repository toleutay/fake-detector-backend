from fastapi import FastAPI, Form, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import requests
import os
import shutil
import re
import aiohttp
from newspaper import Article

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
#  ЧИТАЕМ КЛЮЧИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ
# ============================================
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
SIGHTENGINE_USER = os.environ.get("SIGHTENGINE_USER")
SIGHTENGINE_SECRET = os.environ.get("SIGHTENGINE_SECRET")

if not GOOGLE_API_KEY or not SIGHTENGINE_USER or not SIGHTENGINE_SECRET:
    raise RuntimeError("Missing required environment variables")
# ============================================

def is_url(text: str) -> bool:
    url_pattern = re.compile(r'https?://[^\s]+')
    return bool(url_pattern.match(text))

async def extract_text_from_url(url: str):
    try:
        article = Article(url)
        article.download()
        article.parse()
        return article.title + ". " + article.text[:1000]
    except:
        return None

@app.post("/check")
async def check_content(
    text: str = Form(None),
    file: UploadFile = File(None)
):
    if text is None and file is None:
        raise HTTPException(status_code=400, detail="Нет данных")

    # ------------------ ТЕКСТ / ССЫЛКА ------------------
    if text:
        if is_url(text):
            extracted = await extract_text_from_url(text)
            if extracted:
                text = extracted
            else:
                return {"error": "Не удалось прочитать страницу"}

        url = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
        params = {
            "key": GOOGLE_API_KEY,
            "query": text,
            "languageCode": "ru"
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                data = await resp.json()
                claims = data.get("claims", [])
                if not claims:
                    return {"type": "text", "result": "Ничего не найдено."}
                results = []
                for claim in claims[:3]:
                    review = claim.get("claimReview", [{}])[0]
                    results.append({
                        "text": claim.get("text", ""),
                        "verdict": review.get("textualRating", "нет данных"),
                        "source": review.get("publisher", {}).get("name", "неизвестно"),
                        "link": review.get("url", "")
                    })
                return {"type": "text", "result": results}

    # ------------------ ФАЙЛ (ФОТО/ВИДЕО) через Sightengine ------------------
    if file:
        temp_path = f"temp_{file.filename}"
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        try:
            ext = file.filename.split('.')[-1].lower()
            media_type = "video" if ext in ["mp4", "mov", "avi", "wmv", "mkv"] else "image"

            with open(temp_path, "rb") as f:
                files = {"media": f}
                params = {
                    "api_user": SIGHTENGINE_USER,
                    "api_secret": SIGHTENGINE_SECRET,
                    "models": "genai",  # правильная модель для AI-генерации
                }
                response = requests.post(
                    "https://api.sightengine.com/1.0/check.json",
                    files=files,
                    params=params,
                    timeout=30
                )

            if response.status_code != 200:
                raise Exception(f"Sightengine error: {response.text}")

            result = response.json()
            confidence = result.get("type", {}).get("ai_generated", 0.5)
            is_fake = confidence > 0.7

            return {
                "type": media_type,
                "is_fake": is_fake,
                "confidence": confidence,
                "details": result
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)