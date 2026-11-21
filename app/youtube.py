"""YouTube Data API v3: OAuth 연동 · 예약 업로드 · 채널 분석 · 댓글 답글.

구글 규정상 처음 한 번은 본인 채널로 로그인(동의)이 필요하다.
인증 전(테스트 모드)에는 API 업로드가 '비공개'로만 올라갈 수 있어, 대시보드에서 공개 전환을 안내한다.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import settings

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

KST = timezone(timedelta(hours=9))


class YouTubeError(RuntimeError):
    pass


def _libs():
    try:
        from google.auth.transport.requests import Request  # type: ignore
        from google.oauth2.credentials import Credentials  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
    except Exception as e:  # pragma: no cover
        raise YouTubeError(
            "유튜브 라이브러리가 설치되지 않았습니다. start.command 로 다시 설치해 주세요."
        ) from e
    return Request, Credentials, build


def status() -> dict:
    tok = settings.youtube_token_path()
    secret = settings.client_secret_path()
    info = {"client_secret": secret.exists(), "client_secret_path": str(secret),
            "token": tok.exists(), "token_path": str(tok), "channel": None, "ok": False}
    if tok.exists():
        try:
            Request, Credentials, build = _libs()
            creds = Credentials.from_authorized_user_file(str(tok), SCOPES)
            if creds and creds.valid:
                svc = build("youtube", "v3", credentials=creds, cache_discovery=False)
                resp = svc.channels().list(part="snippet,statistics", mine=True).execute()
                if resp.get("items"):
                    ch = resp["items"][0]
                    info["channel"] = {
                        "id": ch["id"],
                        "title": ch["snippet"]["title"],
                        "subs": ch.get("statistics", {}).get("subscriberCount"),
                        "videos": ch.get("statistics", {}).get("videoCount"),
                        "views": ch.get("statistics", {}).get("viewCount"),
                    }
                    info["ok"] = True
                else:
                    info["ok"] = True
        except Exception as e:
            info["error"] = str(e)
    return info


def authorize(open_browser: bool = True) -> dict:
    """브라우저 동의 창을 띄워 OAuth 토큰을 저장한다(최초 1회)."""
    from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore

    secret = settings.client_secret_path()
    if not secret.exists():
        raise YouTubeError(
            f"client_secret.json 이 없습니다: {secret}\n"
            "구글 클라우드 콘솔 → 사용자 인증 정보 → OAuth 클라이언트 ID(데스크톱 앱) 를 만들어 "
            "config/client_secret.json 으로 저장해 주세요."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=open_browser,
                                  authorization_prompt_message="브라우저에서 유튜브 채널 로그인을 완료해 주세요.")
    tok = settings.youtube_token_path()
    tok.parent.mkdir(parents=True, exist_ok=True)
    tok.write_text(creds.to_json(), encoding="utf-8")
    try:
        import os

        os.chmod(tok, 0o600)
    except Exception:
        pass
    return {"ok": True, "token_path": str(tok)}


def _service():
    Request, Credentials, build = _libs()
    tok = settings.youtube_token_path()
    if not tok.exists():
        raise YouTubeError("유튜브 로그인이 필요합니다. 대시보드에서 [유튜브 로그인] 을 눌러 주세요.")
    creds = Credentials.from_authorized_user_file(str(tok), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            tok.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise YouTubeError("유튜브 인증이 만료되었습니다. 다시 [유튜브 로그인] 을 눌러 주세요.")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def schedule_time(day: datetime | None = None, hour: int | None = None, minute: int | None = None) -> datetime:
    s = settings.load_settings()["youtube"]
    hour = int(s.get("default_hour", 19) if hour is None else hour)
    minute = int(s.get("default_minute", 0) if minute is None else minute)
    base = day or datetime.now(KST)
    dt = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if dt <= datetime.now(KST) + timedelta(minutes=10):
        dt += timedelta(days=1)
    return dt


def upload(video_path: Path, title: str, description: str, tags: list[str],
           publish_at: datetime | None = None, privacy: str | None = None,
           thumbnail: Path | None = None, made_for_kids: bool = False) -> dict:
    svc = _service()
    s = settings.load_settings()["youtube"]
    if publish_at is not None:
        privacy_status = "private"          # 예약 공개는 private + publishAt 조합만 허용
    else:
        privacy_status = privacy or s.get("default_privacy", "private")
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": tags[:30],
            "categoryId": s.get("category_id", "24"),
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": bool(made_for_kids),
            "license": "youtube",
        },
    }
    if publish_at is not None:
        body["status"]["publishAt"] = _rfc3339(publish_at)

    from googleapiclient.http import MediaFileUpload  # type: ignore

    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True, chunksize=4 * 1024 * 1024)
    req = svc.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        st, response = req.next_chunk()
    vid = response.get("id")
    if thumbnail and Path(thumbnail).exists():
        try:
            svc.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumbnail))).execute()
        except Exception as e:
            response["thumbnail_error"] = str(e)
    return {
        "id": vid,
        "url": f"https://youtube.com/shorts/{vid}",
        "privacy": privacy_status,
        "publish_at": body["status"].get("publishAt"),
        "note": ("예약 업로드가 걸렸습니다. 구글 앱 인증 전이면 스튜디오에서 예약 상태를 확인하세요."
                 if publish_at else ""),
    }


# ------------------------------------------------------------------ 채널 분석
def channel_videos(max_results: int = 50) -> list[dict]:
    svc = _service()
    ch = svc.channels().list(part="contentDetails,statistics,snippet", mine=True).execute()
    if not ch.get("items"):
        raise YouTubeError("내 채널을 찾을 수 없습니다. 로그인 채널을 확인해 주세요.")
    item = ch["items"][0]
    uploads = item["contentDetails"]["relatedPlaylists"]["uploads"]
    out = []
    token = None
    while len(out) < max_results:
        pl = svc.playlistItems().list(part="snippet,contentDetails", playlistId=uploads,
                                      maxResults=min(50, max_results - len(out)), pageToken=token).execute()
        ids = [i["contentDetails"]["videoId"] for i in pl.get("items", [])]
        if ids:
            vs = svc.videos().list(part="snippet,statistics,contentDetails,status", id=",".join(ids)).execute()
            for v in vs.get("items", []):
                st = v.get("statistics", {})
                out.append({
                    "id": v["id"],
                    "title": v["snippet"]["title"],
                    "published": v["snippet"]["publishedAt"],
                    "views": int(st.get("viewCount", 0)),
                    "likes": int(st.get("likeCount", 0)),
                    "comments": int(st.get("commentCount", 0)),
                    "duration": v.get("contentDetails", {}).get("duration"),
                    "privacy": v.get("status", {}).get("privacyStatus"),
                })
        token = pl.get("nextPageToken")
        if not token:
            break
    return out


def analytics_summary(days: int = 28) -> dict:
    """YouTube Analytics API (권한이 있을 때만). 실패는 조용히 넘긴다."""
    try:
        Request, Credentials, build = _libs()
        tok = settings.youtube_token_path()
        creds = Credentials.from_authorized_user_file(str(tok), SCOPES)
        if not creds.valid:
            creds.refresh(Request())
        svc = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
        end = datetime.now(KST).date()
        start = end - timedelta(days=days)
        r = svc.reports().query(
            ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
            metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained",
            dimensions="day", sort="day",
        ).execute()
        rows = r.get("rows", [])
        return {"rows": rows, "headers": [h["name"] for h in r.get("columnHeaders", [])], "ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def comments(video_id: str | None = None, max_results: int = 50) -> list[dict]:
    svc = _service()
    kwargs = {"part": "snippet,replies", "maxResults": min(100, max_results), "textFormat": "plainText",
              "order": "time"}
    if video_id:
        kwargs["videoId"] = video_id
    else:
        kwargs["allThreadsRelatedToChannelId"] = "mine"
    try:
        resp = svc.commentThreads().list(**kwargs).execute()
    except Exception as e:
        raise YouTubeError(f"댓글을 불러오지 못했습니다: {e}")
    out = []
    for it in resp.get("items", []):
        s = it["snippet"]["topLevelComment"]["snippet"]
        out.append({
            "thread_id": it["id"],
            "comment_id": it["snippet"]["topLevelComment"]["id"],
            "video_id": s.get("videoId"),
            "author": s.get("authorDisplayName"),
            "text": s.get("textDisplay"),
            "published": s.get("publishedAt"),
            "likes": s.get("likeCount", 0),
            "replied": bool(it.get("replies")),
        })
    return out


def draft_reply(text: str) -> str:
    """한국어 댓글에 어울리는 재치 답글 초안(기존 호환)."""
    return draft_replies(text)["ko"]


def draft_replies(text: str) -> dict:
    """댓글 언어에 맞춰 한국어·영어 답글 초안을 각각 만든다."""
    low = (text or "").lower()
    is_english = not re.search(r"[가-힣]", text or "") and re.search(r"[a-zA-Z]{2,}", text or "") is not None
    region = ""
    m = re.search(r"(서울|부산|대구|인천|광주|대전|울산|세종|제주|경기|강원|충북|충남|전북|전남|경북|경남)", text or "")
    if m:
        region = m.group(1)

    ko_templates = {
        "region": (f"{region + '에서' if region else '오늘도'} 같이 봐 주셔서 고맙습니다 🌷 "
                   "내일도 같은 시간에 찾아뵐게요. 알림 설정해 두시면 놓치지 않으세요!"),
        "funny": "ㅋㅋㅋ 이 댓글 보고 저도 한 번 더 웃었어요 😆 다음 편은 더 웃기게 준비할게요!",
        "like": "따뜻한 댓글 감사합니다 😊 오늘 하루도 맛있게 보내세요!",
        "request": "좋은 의견 감사합니다! 다음 편에 꼭 넣어 볼게요 🙌 (기획 메모 완료!)",
        "generic": "소중한 댓글 감사합니다. 다음 편에서 또 만나요 🌷",
    }
    en_templates = {
        "region": f"Thanks for watching from {region or 'wherever you are'} 🌍 Same time tomorrow! Turn on notifications so you don't miss it.",
        "funny": "Haha, this comment made my day 😆 More chaos coming in the next episode!",
        "like": "Thank you so much! 😊 Have a delicious day!",
        "request": "Great idea — adding it to the next episode list 🙌",
        "generic": "Thanks for the love! See you in the next one 🌷",
    }
    ko_key = "generic"
    if any(k in text for k in ("지역", "어디", "출근", "퇴근")):
        ko_key = "region"
    elif any(k in text for k in ("웃기", "재밌", "ㅋㅋ", "최고", "미쳤")):
        ko_key = "funny"
    elif any(k in text for k in ("좋아", "감사", "사랑", "맛있")):
        ko_key = "like"
    elif any(k in text for k in ("다음", "추천", "편", "해주세요")):
        ko_key = "request"

    en_key = "generic"
    if any(k in low for k in ("where", "from", "region", "country")):
        en_key = "region"
    elif any(k in low for k in ("funny", "lol", "haha", "😂", "🤣")):
        en_key = "funny"
    elif any(k in low for k in ("love", "like", "delicious", "yummy", "best")):
        en_key = "like"
    elif any(k in low for k in ("next", "please", "suggest", "try")):
        en_key = "request"

    return {
        "ko": ko_templates[ko_key],
        "en": en_templates[en_key],
        "language": "en" if is_english else "ko",
        "suggested": "en" if is_english else "ko",
    }


def reply(comment_id: str, text: str) -> dict:
    svc = _service()
    resp = svc.comments().insert(part="snippet", body={
        "snippet": {"parentId": comment_id, "textOriginal": text}
    }).execute()
    return {"id": resp.get("id"), "text": text}


def post_comment(video_id: str, text: str) -> dict:
    """영상에 첫 댓글(고정댓글 문구)을 자동 게시한다.

    YouTube Data API 에는 '고정(pin)' 엔드포인트가 없어, 댓글 게시까지만 자동화한다.
    (핀 고정은 유튜브 앱/웹에서 1클릭)
    """
    svc = _service()
    resp = svc.commentThreads().insert(
        part="snippet",
        body={"snippet": {"videoId": video_id,
                          "topLevelComment": {"snippet": {"textOriginal": text}}}},
    ).execute()
    return {"id": resp.get("id"), "video_id": video_id}


def video_status(video_id: str) -> dict:
    svc = _service()
    resp = svc.videos().list(part="status,statistics", id=video_id).execute()
    items = resp.get("items") or []
    if not items:
        return {}
    v = items[0]
    return {"privacy": v.get("status", {}).get("privacyStatus"),
            "publish_at": v.get("status", {}).get("publishAt"),
            "views": v.get("statistics", {}).get("viewCount")}


def recent_uploads(days: int = 30) -> list[dict]:
    vids = channel_videos(100)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for v in vids:
        try:
            dt = datetime.fromisoformat(v["published"].replace("Z", "+00:00"))
        except Exception:
            continue
        if dt >= cutoff:
            out.append(v)
    return out
