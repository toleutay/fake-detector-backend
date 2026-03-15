from fastapi import FastAPI, Form, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sightengine.client import SightengineClient
import os
import shutil
import re
import aiohttp
from newspaper import Article

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ==== ТВОИ КЛЮЧИ (после регистрации) ====
GOOGLE_API_KEY = "AIzaSyCbB73U2coWJbxIdBS9IFQOwq96uRfgJrc"          # старый ключ Google
SIGHTENGINE_USER = "29521101"     # с сайта sightengine
SIGHTENGINE_SECRET = "pmTAeETaCKv7UDQXFwFdgYaJsEoVvTni" # с сайта sightengine
# ========================================

client = SightengineClient(SIGHTENGINE_USER, SIGHTENGINE_SECRET)

def is_url(text: str) -> bool:
    return bool(re.compile(r'https?://[^\s]+').match(text))

async def extract_text_from_url(url: str):
    try:
        article = Article(url)
        article.download()
        article.parse()
        return article.title + ". " + article.text[:1000]
    except:
        return None

@app.post("/check")
async def check_content(text: str = Form(None), file: UploadFile = File(None)):
    if text is None and file is None:
        raise HTTPException(status_code=400, detail="Нет данных")

    # ----- ТЕКСТ / ССЫЛКА (Google Fact Check) -----
    if text:
        if is_url(text):
            extracted = await extract_text_from_url(text)
            text = extracted if extracted else text

        params = {"key": GOOGLE_API_KEY, "query": text, "languageCode": "ru"}
        async with aiohttp.ClientSession() as session:
            async with session.get("https://factchecktools.googleapis.com/v1alpha1/claims:search", params=params) as resp:
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

    # ----- ФАЙЛ (ФОТО/ВИДЕО) через Sightengine -----
    if file:
        temp_path = f"temp_{file.filename}"
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        try:
            ext = file.filename.split('.')[-1].lower()
            is_video = ext in ["mp4", "mov", "avi", "wmv", "mkv"]
            media_type = "video" if is_video else "image"

            # Для видео до 1 минуты используем синхронный режим
            if is_video:
                # передаём локальный файл (библиотека умеет работать с файлами)
                result = client.check('deepfake').video_sync(temp_path)
            else:
                result = client.check('deepfake').set_file(temp_path)

            # Извлекаем вероятность deepfake
            confidence = result.get('type', {}).get('deepfake', 0.5)
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