import asyncio
import json
import os
import time
import hashlib
import base64
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import httpx
import jwt
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

# ================= CONFIG =================
REQUEST_TIMEOUT = 30
SALT = "j8n5HxYA0ZVF"
ENCRYPTION_KEY = "6fbJwIfT6ibAkZo1VVKlKVl8M2Vb7GSs"

FAIRBID_BURST = int(os.getenv("FAIRBID_BURST", "1"))
FAIRBID_DELAY = float(os.getenv("FAIRBID_DELAY", "0"))
GLOBAL_CONCURRENCY = int(os.getenv("GLOBAL_CONCURRENCY", "3"))
SEMAPHORE = asyncio.Semaphore(GLOBAL_CONCURRENCY)

# Telegram (fail-fast if missing)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise RuntimeError("❌ Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in env")

# ================= ACCOUNTS =================
# Trailing spaces manually removed from URLs below.
ACCOUNTS = [
    {
        "NAME": "cashthug",
        "JSON_URL": "https://raw.githubusercontent.com/CodeWithAlone1/Crazy/a04be8177a2dc8c80ca97b769ea46974037c3aa0/Cashthug.json",
        "FIREBASE_KEY": "AIzaSyDArVb852ZEA9s4bV9NozW0-lVmX1UtsIg",
        "PROJECT_ID": "quiz-cash-d2b1f",
        "REFRESH_TOKEN": "AMf-vBzTcunK7jciYFZ9qIh7bwelwUO5bPZpUYkiq429qYK--_LfpF3pIT0U0ZImfhHb2jpLwhsGAbs1XmsGsahRrEBooeCpHgFnZiDSQ4Uxctgv6L4J4b6nWv0Gbp7VIvr2Ue7SexjWrniFN1P9mhgZL4QP8WitY9G2YnHxKx67toWOaxQVRiC8ETLsQNyp52vtsUu720ODB_T6J--bHuo90zsLAklUXcLM6DK5AKGJ75G2A1g54205H_wTVdQN32Qyzxs__aeZTgbDV53cQ6EL18fMuoVY_7RYuWUwx7yQjYMKDriCehbzl_n0boasFgFG-RW6Yskc3RWg0wr25JwphrdfgLdYu5qcFGPXkG1fUHIuaZ5PwbYKPu2u0PKjsrYjyyZMKr9i0yv_wYDSSB5MAUKsy5qZ2NDJKr0tKSSPhrQvQnApOIw",
        "BASE_URL": "https://fairbid.inner-active.mobi/simpleM2M/fyberMediation",
        "SPOT_ID": "2238091"
    },
    {
        "NAME": "cashmonkey",
        "JSON_URL": "https://raw.githubusercontent.com/CodeWithAlone1/Crazy/5597dc3c9b03b418cb535c8e357f72408f2bec79/Tapearn.json",
        "FIREBASE_KEY": "AIzaSyA80VcSTBCCc2eK5urAdPuVFDWo1F9gAds",
        "PROJECT_ID": "cash-monkey-1df41",
        "REFRESH_TOKEN": "AMf-vBzUnWKzlxBv-hY_KexzcZh9q2zQPfdByAjG1KBKzlStAsfnJJmfx6ozmkxp7VKNbgnPOCj11uMUTMCDCVLQoufAZPA1BkaDRML_LNpvOVLm_6MFPDHLv-eIV44R1cE-j0sMYB6yX8qO_w9cNZjktrcBe21bvmO0y1hnzUc1ant2weqvfizYDloknlfgYrawzHY5n_YLrhuTfiNOeXyNEHjB00WfgmijCESuN6pXcWYuK_8J5dNcpvAov96JIS9ROkYUtLY8BZa4snhl2oQ8VPnP9MRwELGsYWzqKUGcYgCk3xWu1Ie2Wzwf9oqlSSOYULYQI_GNoQkUThUOPtowzGF-t--8o12-5N1rSuROIKA-UokCMmyWZ6CeplRjdypalBCo3xjO05tICDNwe2N8-HAOHlaaGc19sM8Sc6JwjAEqAudfaDg",
        "BASE_URL": "https://fairbid.inner-active.mobi/simpleM2M/fyberMediation",
        "SPOT_ID": "2052854"
    },
    {
        "NAME": "cashmonkey1",
        "JSON_URL": "https://raw.githubusercontent.com/CodeWithAlone1/Crazy/c479f0312ea53d5411d17073c9795d971a01a9a2/Cashmonkey1.json",
        "FIREBASE_KEY": "AIzaSyA80VcSTBCCc2eK5urAdPuVFDWo1F9gAds",
        "PROJECT_ID": "cash-monkey-1df41",
        "REFRESH_TOKEN": "AMf-vBw7Q4f4edypi--KyaR11rNpT1hboGzOfMY9c3zCdydmnfFAaDpD85cgsxy_UmAfJwk5V8Dcu1xcU_LkWXiiwRkvgkMg1K67TM4xsor7GT7le1MPx_XwhV4PNJ2R6qbKSIipEjZuo_fsgP7dsQD9RUkAsYr8YQQvCAt2tmCMh56SrC9n7np5Hg7SR_M_BBbQU21jH-TjHqoSZeI9zncK7Yo5XupEh60DL7wcQk75YEizWsMqMHJRMQBVSKz7XiGxpifhVKSa7VgsIGvquf9iIorj56OuI1imUgnZ6fHbQo6zqQ8xzyGWzYtpla_nYapuDUpLz3AcqTE75ABEPiJM_1wn22m1qC8IqvfxKcztjxqgUrK2VVysYchdKdxr4BraRxZLJZnKB-daXGixNDZN6Kj4YIktPPUJpmy8hjjMttVLoYIy6mo",
        "BASE_URL": "https://fairbid.inner-active.mobi/simpleM2M/fyberMediation",
        "SPOT_ID": "2052855"
    },
    {
        "NAME": "cashthug1",
        "JSON_URL": "https://raw.githubusercontent.com/CodeWithAlone1/Crazy/36c09005538d67efee0a24a40c81556d17e7f2e6/Cashthug1.json",
        "FIREBASE_KEY": "AIzaSyDArVb852ZEA9s4bV9NozW0-lVmX1UtsIg",
        "PROJECT_ID": "quiz-cash-d2b1f",
        "REFRESH_TOKEN": "AMf-vBwMxm-f_FGGxI2TAucMlcLpaTjXGSKirzLBwWa-eQoRTg_IiADdlVsy4ckhetbvTl5PIOw-agDwsN4g1wAeK8zWt8WQZupj6sAzyGoQydSqin4BxlKacx1KhymPmKxRzpdVsYx7d8BQRHiFPGfqlwL6yiIESLOfsARaET6AslQC0uUtvBsrGbp4veCNiZA1I71qlhPgoTmLJOKq1umX2MOAUy2Sso5UFvV_YuBDf_FB2lTsBiiBU0CxGkQZn3N7UIS2OKnJM1o8ipuwtYs1nnB16yf7B4WIDc6L4Xg-IXel93fLuMYUmUA-pwMkOcBezvRuKtsD7-O6fLF1-Cnw5ipBXv5C3F1RRwh8FHDmpftjjHjYSt2TfttEp547Pw3-Afcw4exxCwAv64dPKNPbMxNgoB3DJ-PFle0zKHyNcSevyyo1wZo",
        "BASE_URL": "https://fairbid.inner-active.mobi/simpleM2M/fyberMediation",
        "SPOT_ID": "2238092"
    },
    {
        "NAME": "puzzlemaster",
        "JSON_URL": "https://raw.githubusercontent.com/CodeWithAlone1/Crazy/96c3591a649366c2dc9c1f116a114c476b80022c/Puzzlemaster.json",
        "FIREBASE_KEY": "AIzaSyCF-M9WFi6IsTIn7G3hzG_nIi3rWA3XD6o",
        "PROJECT_ID": "puzzle-master-51426",
        "REFRESH_TOKEN": "AMf-vBz0TDOcx7Bk1bjuksQxIX3bfZRnsKd7pPdxQfexCPD_XeuFSQRvme1EyvWcMdIDJShbchIohjRddnn5PEMBeBvz2L44f-ciuG5hev9dWN2YkIgBsErwYsbMmZqs4LNQ3kJKfZqbtH6pSPPuUdxwUnzQxKD9HCmdgIHQ16URA765WsZncYH6xbLf41-b59_5S-WXM3yTmUT3JHEOol3zHw2CmZQid50ZmWdF_u_9rBD-z-RnkGeknYewQZxkF0F9TmLd4AVuTeiDHJqrcsZl9Mg_T16Y4rqAWjm3yr1zv0kdexvQaXc1BociVZbkCI8uFXEPjAxLLW0MNItv52vu2DwlH-BN-1rO4q09Wg5RkB2yLdfbERxq_D-waV1EKIWJGleXWIEwIq0ZiIxTKreUVw7lipRirdmqZj_4XtfQEbQiIV2uPh4",
        "BASE_URL": "https://fairbid.inner-active.mobi/simpleM2M/fyberMediation",
        "SPOT_ID": "2555630"
    },
    {
        "NAME": "tapearn1",
        "JSON_URL": "https://gist.githubusercontent.com/CodeWithAlone1/22b6346df60c525bb524f19cb31cf393/raw/1549e166b1c88ca4770fe2136bb3b8c8cadab711/Tapearn1.json",
        "FIREBASE_KEY": "AIzaSyAkSwrPZkuDYUGWU65NAVtbidYzE5ydIJ4",
        "PROJECT_ID": "cash-rhino",
        "REFRESH_TOKEN": "AMf-vBySjV-UPhWojUd4nZpayGUNrDKmmI093Hf6i0yr7UhieePaNRaQW_K4PO224uDdGdVU1nM1HVBKKdtugZb-1_EZga2rRkfgL1oILMBZ3lJOjcsunwJG90RkbJXRFX8QcRbUaGvoO1UawbmbN4Vh9MU8LC9KMZCOo93MzdZ5AwPTqS_X9g8RnqbFAoV0EItom8NS-LP6d3HjTTEgAfydZJHTNLRj-Sm8qepUdGn4X9UAm6hTqUFd6hiApGO_LNanDOW_AgWsHuVOQjgYv__j2tRm0SFOCdWN_MTzbpT-XQLAY8sqk4fumKAc18Wt5FPgZ1xNaVFeQKA8vOHKLkEg2SFr_EmM-0Xb_orLxJU1wJCOBFtbn1Y1SPiR6HmqLJh2CYk1t-uPkHVJ4GQM87Nkn4S45iNzxw",
        "BASE_URL": "https://fairbid.inner-active.mobi/simpleM2M/fyberMediation",
        "SPOT_ID": "2238157"
    },
    {
        "NAME": "cashmafia",
        "JSON_URL": "https://gist.githubusercontent.com/CodeWithAlone1/65619512c241d9f329da7d554367da39/raw/fb92588b7adb6755a7b1207691c6aaf2ba853e84/Cashmafia.json",
        "FIREBASE_KEY": "AIzaSyCxoB5Kk1AEQt5c6CHsC-XVdNaxr4nu8Es",
        "PROJECT_ID": "cash-mafia-5ae03",
        "REFRESH_TOKEN": "AMf-vByekbhk-2Z0XivRcnhlQx-hjsm9Tjzdy9yqj0OwR5gNRkTiPB1NZiBn3O2dP1EfwQ1wBZ2GQp8E8vsZXbxEB9KHl1Ef-yyg0a1CHStNwbKq-jWqfm2yUwQgpVKgsKonfpqVYyNYJagD3Ucve-XbT7YvlCTwB754vFKL1ESo8UVSjaJlatVD8UKqnEpIbKWTOQhlIpQuGQqzaJ8VZXV5gwJn-41blO2S13Q8I8pZstNFENCfVaXHGyzYdT4xXU4oAcZ7B-4ke_gMXxP5xs629LZ0Oc6rwIPmQNE1MD3O3KBP6LcYSEfTpm2guwTSmXNapWLXDW2jxDvk8gUs1BYDesAYtYR6npqpQSQyReOATGKvJQCkg_VMWfCUec6VbxyZP0c4I7OieFcPSrgxI8SYIzdJHYv0yfqQI_eCX9k3Bw_WYnGqn9I",
        "BASE_URL": "https://fairbid.inner-active.mobi/simpleM2M/fyberMediation",
        "SPOT_ID": "2099492"
    },
    {
        "NAME": "cashpanda",
        "JSON_URL": "https://gist.githubusercontent.com/CodeWithAlone1/dd7e69603e15f6e8bd6edfc901fe151c/raw/29a515c1dc8b8587028c020034724966c24dc7ae/Cashpanda.json",
        "FIREBASE_KEY": "AIzaSyAMQu13Wg_7UnuwSstH6JKfh37VIEZ4bGg",
        "PROJECT_ID": "cash-panda-76893",
        "REFRESH_TOKEN": "AMf-vByrK0-avbggif5WglYroLLr9sWQx53gVglm-WkChxcULpECQNq6K9hoBT71D7LGKJPh8Zt-TQjXew2WDdObjb9pz8Nx7QcWtahmOJdvVUW3O_yzC-Khpwp_GZ8-anCetMLWi2pOqgxe_LPsGHRAPjJ3Lc_75AaNQsVciYGTpViqpUR5rehVRAogMVaU2127ub3A8f8xXnKzuVCUHla5I-ZGyLVX0qy4Gq00bDihIjqSmDbfw5D4kHcLbEBfowddFsCcT-O7XimL3Bjq9KRKAH5gKmOFinEbKGl7Tt1r9IMOYGTc1eSNjI_AVcetUzlFcLu7ee1g65oFaNiz_f-01-XyoWrWk7aPmNiSqgMP6FwI-EbEaX8ihZzD1bVXjlVH_noLl3qC1tHEs6XAulkqEQ8vMicagmkoqCPxMdd9FZy5S5O1ZD8",
        "BASE_URL": "https://fairbid.inner-active.mobi/simpleM2M/fyberMediation",
        "SPOT_ID": "2042804"
    }
]

# ================= HELPERS =================
def log(name: str, msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{name}] {msg}", flush=True)

async def send_telegram(msg: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            })
    except Exception as e:
        print(f"[SYSTEM] 📵 Telegram send failed: {e}")

async def create_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        http2=True,
        timeout=httpx.Timeout(REQUEST_TIMEOUT),
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=100),
        headers={"User-Agent": "Mozilla/5.0 (Android)"}
    )

async def load_config(client: httpx.AsyncClient, url: str) -> Dict[str, Any]:
    r = await client.get(url.strip())
    r.raise_for_status()
    j = r.json()
    return {
        "user_id": j["client_params"]["publisher_supplied_user_id"],
        "payload": json.dumps(j, separators=(",", ":"))
    }

async def get_id_token(
    client: httpx.AsyncClient,
    firebase_key: str,
    refresh_token: str
) -> tuple[str, str, int]:
    # ✅ FIXED: NO SPACE in key=
    url = f"https://securetoken.googleapis.com/v1/token?key={firebase_key.strip()}"
    r = await client.post(
        url,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    r.raise_for_status()
    j = r.json()
    return j["id_token"], j["user_id"], int(j["expires_in"])

class TokenManager:
    def __init__(self, firebase_key: str, refresh_token: str):
        self.firebase_key = firebase_key
        self.refresh_token = refresh_token
        self.token: Optional[str] = None
        self.uid: Optional[str] = None
        self._lock = asyncio.Lock()

    async def get(self, client: httpx.AsyncClient) -> tuple[str, str]:
        async with self._lock:
            now = time.time()
            needs_refresh = True

            if self.token:
                try:
                    payload = jwt.decode(
                        self.token,
                        options={"verify_signature": False}
                    )
                    exp = payload.get("exp", 0)
                    if exp > now + 120:  # refresh 2 mins before expiry
                        needs_refresh = False
                except Exception:
                    pass

            if needs_refresh:
                log("TokenManager", "🔄 Refreshing token")
                self.token, self.uid, _ = await get_id_token(
                    client, self.firebase_key, self.refresh_token
                )
                try:
                    payload = jwt.decode(self.token, options={"verify_signature": False})
                    exp_dt = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
                    log("TokenManager", f"✅ New token valid until {exp_dt.strftime('%Y-%m-%d %H:%M:%S')}")
                except Exception as e:
                    log("TokenManager", f"⚠️ Decode failed: {e}")
            assert self.token and self.uid
            return self.token, self.uid

def build_hash_payload(user_id: str, url: str) -> str:
    now = int(time.time())
    ts = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    raw = f"{url}{ts}{SALT}"
    h = hashlib.sha512(raw.encode()).hexdigest()
    return json.dumps({"user_id": user_id, "timestamp": now, "hash_value": h}, separators=(",", ":"))

def encrypt_offer(offer_id: str) -> Dict[str, Any]:
    key = hashlib.sha256(ENCRYPTION_KEY.encode()).digest()
    raw = json.dumps({"offerId": offer_id}, separators=(",", ":")).encode()
    cipher = AES.new(key, AES.MODE_ECB)
    encrypted = cipher.encrypt(pad(raw, AES.block_size))
    return {
        "data": {
            "data": base64.b64encode(encrypted).decode()
        }
    }

# ================= API CALLS =================
async def call_with_auth_retry(client: httpx.AsyncClient, method: str, url: str, token: str, **kwargs):
    for attempt in range(2):
        req = getattr(client, method)
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        try:
            r = await req(url, headers=headers, **kwargs)
            if r.status_code == 401 and attempt == 0:
                continue
            r.raise_for_status()
            return r
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401 and attempt == 0:
                continue
            raise
    raise Exception("Auth failed after refresh")

async def get_super_offer(
    client: httpx.AsyncClient,
    token: str,
    project_id: str,
    uid: str
) -> Optional[Dict[str, Any]]:
    url = f"https://firestore.googleapis.com/v1/projects/{project_id.strip()}/databases/(default)/documents/users/{uid.strip()}:runQuery"
    query = {
        "structuredQuery": {
            "from": [{"collectionId": "superOffers"}],
            "where": {
                "fieldFilter": {
                    "field": {"fieldPath": "status"},
                    "op": "NOT_EQUAL",
                    "value": {"stringValue": "COMPLETED"}
                }
            },
            "limit": 1
        }
    }
    r = await call_with_auth_retry(client, "post", url, token, json=query)
    for item in r.json():
        if "document" in item:
            f = item["document"]["fields"]
            return {
                "offerId": f["offerId"]["stringValue"],
                "fees": int(f["fees"]["integerValue"])
            }
    return None

async def get_boosts(
    client: httpx.AsyncClient,
    token: str,
    project_id: str,
    uid: str
) -> int:
    url = f"https://firestore.googleapis.com/v1/projects/{project_id.strip()}/databases/(default)/documents/users/{uid.strip()}?mask.fieldPaths=boosts"
    r = await call_with_auth_retry(client, "get", url, token)
    doc = r.json()
    return int(doc.get("fields", {}).get("boosts", {}).get("integerValue", 0))

async def run_fairbid(client: httpx.AsyncClient, acc: Dict[str, Any], cfg: Dict[str, Any]) -> None:
    async with SEMAPHORE:
        try:
            url = f"{acc['BASE_URL'].strip()}?spotId={acc['SPOT_ID']}"
            r = await client.post(url, content=cfg["payload"], timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            text = r.text

            tasks = []
            if 'impression":"' in text:
                imp_url = text.split('impression":"')[1].split('"')[0]
                tasks.append(client.get(imp_url, timeout=10))
            if 'completion":"' in text:
                comp_url = text.split('completion":"')[1].split('"')[0]
                payload = build_hash_payload(cfg["user_id"], comp_url)
                tasks.append(client.post(comp_url, content=payload, timeout=10))

            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for i, res in enumerate(results):
                    if isinstance(res, Exception):
                        log(acc["NAME"], f"⚠️ Sub-request {i} failed: {res}")

        except Exception as e:
            log(acc["NAME"], f"❌ FairBid error: {e}")
            await send_telegram(f"❌ <b>{acc['NAME']}</b>\nFairBid error: {e}")

async def call_fn(
    client: httpx.AsyncClient,
    token: str,
    project_id: str,
    name: str,
    offer_id: str
) -> Dict[str, Any]:
    url = f"https://us-central1-{project_id.strip()}.cloudfunctions.net/{name}"
    r = await call_with_auth_retry(client, "post", url, token, json=encrypt_offer(offer_id))
    return r.json()

# ================= HEALTH CHECK SERVER =================
import aiohttp
from aiohttp import web

START_TIME = time.time()

async def health_check(request):
    uptime = time.time() - START_TIME
    return web.json_response({
        "status": "ok",
        "uptime_seconds": round(uptime, 2),
        "accounts": len(ACCOUNTS),
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

async def start_web_server():
    app = web.Application()
    app.router.add_get("/health", health_check)
    app.router.add_get("/", health_check)
    port = int(os.getenv("PORT", "8001"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.1", port)
    await site.start()
    log("SYSTEM", f"🌐 Health server running on port {port}")

# ================= MAIN LOOP PER ACCOUNT =================
async def bot_loop(acc: Dict[str, Any]) -> None:
    client = await create_client()
    try:
        cfg = await load_config(client, acc["JSON_URL"])
        tm = TokenManager(acc["FIREBASE_KEY"], acc["REFRESH_TOKEN"])
        log(acc["NAME"], "🟢 STARTED")

        while True:
            try:
                token, uid = await tm.get(client)
                offer = await get_super_offer(client, token, acc["PROJECT_ID"], uid)
                if not offer:
                    await asyncio.sleep(5)
                    continue

                log(acc["NAME"], f"🎯 OFFER FOUND | ID={offer['offerId']} | FEES={offer['fees']}")
                await send_telegram(
                    f"🎯 <b>{acc['NAME']}</b>\n"
                    f"Offer: <code>{offer['offerId']}</code>\n"
                    f"Fees: {offer['fees']}"
                )

                target = offer["fees"] + 1
                boosts = 0

                while boosts < target:
                    boosts = await get_boosts(client, token, acc["PROJECT_ID"], uid)
                    log(acc["NAME"], f"⚡ BOOSTS {boosts}/{target}")
                    if boosts >= target:
                        break

                    log(acc["NAME"], f"🌀 Running FairBid burst ×{FAIRBID_BURST}...")
                    await asyncio.gather(
                        *(run_fairbid(client, acc, cfg) for _ in range(FAIRBID_BURST)),
                        return_exceptions=True
                    )
                    if FAIRBID_DELAY > 0:
                        await asyncio.sleep(FAIRBID_DELAY)

                log(acc["NAME"], "🔓 Unlocking...")
                unlock = await call_fn(client, token, acc["PROJECT_ID"], "superOffer_unlock", offer["offerId"])
                status = unlock.get("status", "OK")
                log(acc["NAME"], f"🔓 UNLOCK → {status}")
                await send_telegram(
                    f"🔓 <b>{acc['NAME']}</b>\n"
                    f"Unlock Status: <b>{status}</b>\n"
                    f"Offer: <code>{offer['offerId']}</code>"
                )

                log(acc["NAME"], "🏆 Claiming...")
                claim = await call_fn(client, token, acc["PROJECT_ID"], "superOffer_claim", offer["offerId"])
                reward = claim.get("reward", "??")
                log(acc["NAME"], f"🏆 CLAIM → Reward: {reward}")
                await send_telegram(
                    f"🏆 <b>{acc['NAME']}</b>\n"
                    f"Reward: <b>{reward}</b>\n"
                    f"Offer: <code>{offer['offerId']}</code>"
                )

                await asyncio.sleep(3)

            except Exception as e:
                error_msg = (
                    f"💥 <b>{acc['NAME']}</b>\n"
                    f"Error: <code>{type(e).__name__}: {e}</code>"
                )
                log(acc["NAME"], f"💥 Inner loop error: {e}")
                await send_telegram(error_msg[:1000])
                await asyncio.sleep(10)

    except Exception as e:
        error_msg = f"🚨 <b>{acc['NAME']}</b>\nCRASHED: {e}"
        log(acc["NAME"], f"🚨 Bot crashed: {e}")
        await send_telegram(error_msg[:1000])
    finally:
        await client.aclose()

# ================= ENTRY =================
async def main() -> None:
    log("SYSTEM", f"🚀 Starting — {len(ACCOUNTS)} accounts")
    log("SYSTEM", f"⚙️ Burst={FAIRBID_BURST}, Delay={FAIRBID_DELAY}, Concurrency={GLOBAL_CONCURRENCY}")
    log("SYSTEM", "✅ Telegram alerts enabled")

    # 🧹 Sanitize URLs at startup (defense in depth)
    for acc in ACCOUNTS:
        acc["JSON_URL"] = acc["JSON_URL"].strip()
        acc["BASE_URL"] = acc["BASE_URL"].strip()

    try:
        await start_web_server()
        await send_telegram("🟢 <b>BOT STARTED</b>\nAll accounts initialized.")
        await asyncio.gather(*(bot_loop(acc) for acc in ACCOUNTS), return_exceptions=True)
    except KeyboardInterrupt:
        log("SYSTEM", "🛑 Shutting down...")
        await send_telegram("🛑 <b>BOT STOPPED</b>\nManual shutdown.")
    except Exception as e:
        await send_telegram(f"🔥 <b>FATAL SYSTEM ERROR</b>\n{e}")
        log("SYSTEM", f"🔥 Fatal error: {e}")

if __name__ == "__main__":

    asyncio.run(main())


