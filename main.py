from fastapi import FastAPI, Form, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import requests
import os
import shutil
import re
import time
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
#  ТВОИ КЛЮЧИ – ВСТАВЬ ИХ СЮДА
# ============================================
import os

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
RESEMBLE_API_KEY = os.environ.get("RESEMBLE_API_KEY")
BASE_URL = "https://app.resemble.ai/api/v2"    # правильный базовый URL
# ============================================

def upload_to_temp_sh(file_path: str) -> str:
    """Загружает файл на temp.sh и возвращает публичную HTTPS-ссылку"""
    with open(file_path, "rb") as f:
        files = {"file": f}
        response = requests.post("https://temp.sh/upload", files=files, timeout=30)
    if response.status_code != 200:
        raise Exception(f"Ошибка загрузки на temp.sh: {response.text}")
    return response.text.strip()

def create_detection(file_url: str, media_type: str) -> str:
    """Создаёт задачу детекции с явным указанием типа"""
    url = f"{BASE_URL}/detect"
    headers = {
        "Authorization": f"Bearer {RESEMBLE_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {"url": file_url}
    if media_type == "image":
        payload["model_types"] = "image"
        payload["use_reverse_search"] = True   # ищем похожие изображения в сети
    elif media_type == "video":
        payload["model_types"] = "talking_head"
        payload["frame_length"] = 2
    # для аудио параметры не нужны
    
    print("Payload to Resemble:", payload)  # отладка
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    if response.status_code != 200:
        raise Exception(f"Ошибка создания задачи: {response.text}")
    return response.json()["item"]["uuid"]

def get_detection_result(uuid: str, media_type: str, timeout=30) -> dict:
    """Получает результат, извлекает confidence и label"""
    url = f"{BASE_URL}/detect/{uuid}"
    headers = {"Authorization": f"Bearer {RESEMBLE_API_KEY}"}
    start = time.time()
    while time.time() - start < timeout:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            raise Exception(f"Ошибка получения результата: {response.text}")
        data = response.json()
        item = data["item"]
        status = item["status"]
        if status == "completed":
            if media_type == "image":
                image_metrics = item.get("image_metrics", {})
                confidence = image_metrics.get("score", 0.5)
                label = image_metrics.get("label", "unknown")
            elif media_type == "video":
                video_metrics = item.get("video_metrics", {})
                confidence = video_metrics.get("score", 0.5)
                label = video_metrics.get("label", "unknown")
            else:
                metrics = item.get("metrics", {})
                confidence = float(metrics.get("aggregated_score", 0.5))
                label = metrics.get("label", "unknown")
            return {"item": item, "confidence": confidence, "label": label}
        elif status == "failed":
            raise Exception("Анализ не удался: " + item.get("error_message", ""))
        time.sleep(2)
    raise TimeoutError(f"Превышено время ожидания для UUID {uuid}")

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

    # ------------------ ФАЙЛ (ФОТО/ВИДЕО) ------------------
    if file:
        temp_path = f"temp_{file.filename}"
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        try:
            ext = file.filename.split('.')[-1].lower()
            media_type = "video" if ext in ["mp4", "mov", "avi", "wmv", "mkv"] else "image"

            # 1. Загружаем на temp.sh
            file_url = upload_to_temp_sh(temp_path)
            print("File URL:", file_url)

            # 2. Создаём задачу
            uuid = create_detection(file_url, media_type)

            # 3. Получаем результат
            result = get_detection_result(uuid, media_type, timeout=30)

            is_fake = result["label"] == "fake"
            confidence = result["confidence"]

            return {
                "type": media_type,
                "is_fake": is_fake,
                "confidence": confidence,
                "details": result["item"]
            }

        except TimeoutError as e:
            return {"type": "pending", "message": str(e), "uuid": uuid}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)