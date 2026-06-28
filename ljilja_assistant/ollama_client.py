from __future__ import annotations

from typing import Any
import json
import re
import urllib.error
import urllib.request

from .config import Config


SYSTEM_PROMPT = (
    "Ti si Ljilja, lokalni lični AI asistent na Mac mini računaru. "
    "Odgovaraš kratko, jasno i na srpskom, najčešće u 1-3 rečenice. "
    "Ne prikazuj tok razmišljanja, analizu, korake ni interne beleške. "
    "Telegram je glavni kanal, Notion je tvoja dugoročna memorija za zadatke, planove, beleške, činjenice o korisniku i radni kontekst, a Jira se koristi samo na eksplicitnu komandu /jira. "
    "Kada dobiješ Notion kontekst, koristi ga kao memoriju, ali nemoj izmišljati detalje kojih nema. "
    "Ne tvrdi da si upisala nešto u Notion osim ako korisnik to eksplicitno traži kroz zadatak i sistem potvrdi upis."
)

CHAT_TIMEOUT_SECONDS = 20
INTENT_TIMEOUT_SECONDS = 6
LINK_TIMEOUT_SECONDS = 6
CHAT_NUM_PREDICT = 96
INTENT_NUM_PREDICT = 96
LINK_NUM_PREDICT = 120
OLLAMA_KEEP_ALIVE = "1h"


class OllamaClient:
    def __init__(self, config: Config):
        self.config = config

    @staticmethod
    def _clean_output(content: str) -> str:
        cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"(?is)^thinking process\s*:.*?(?=\n\s*(final answer|odgovor)\s*:|\Z)", "", cleaned).strip()
        cleaned = re.sub(r"(?is)^(final answer|odgovor)\s*:\s*", "", cleaned).strip()
        return cleaned

    def _chat(self, messages: list[dict[str, str]], timeout: int = CHAT_TIMEOUT_SECONDS, json_mode: bool = False, num_predict: int | None = None) -> str:
        options: dict[str, Any] = {
            "temperature": 0.0 if json_mode else 0.2,
            "top_p": 0.8,
            "num_ctx": 2048,
        }
        if num_predict is not None:
            options["num_predict"] = num_predict
        payload: dict[str, Any] = {
            "model": self.config.ollama_model,
            "stream": False,
            "think": False,
            "messages": messages,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "options": options,
        }
        if json_mode:
            payload["format"] = "json"
        request = urllib.request.Request(
            f"{self.config.ollama_url.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama API greška {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama nije dostupna na {self.config.ollama_url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError(f"Ollama je istekla posle {timeout}s.") from exc
        except OSError as exc:
            raise RuntimeError(f"Ollama konekcija je pukla: {exc}") from exc

        content = data.get("message", {}).get("content", "").strip()
        return self._clean_output(content)

    def ask(self, text: str, context: str = "") -> str:
        user_content = text
        if context:
            user_content = f"Notion memorija:\n{context}\n\nKorisnik:\n{text}"
        content = self._chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            timeout=CHAT_TIMEOUT_SECONDS,
            num_predict=CHAT_NUM_PREDICT,
        )
        return content or "Tu sam, ali nisam dobila smislen odgovor od lokalnog modela."

    def extract_intent(self, text: str, now_iso: str, timezone: str, context: str = "") -> dict[str, Any]:
        context_block = f"\nKontekst razgovora i Notion memorija:\n{context}\n" if context else ""
        prompt = f"""
Klasifikuj Telegram poruku za privatnog asistenta Ljilju.

Sada: {now_iso}
Zona: {timezone}
{context_block}

Vrati ISKLJUCIVO JSON objekat, bez markdowna i bez dodatnog teksta.

Schema:
{{
  "action": "chat" | "create_item" | "clarify",
  "type": "Task" | "Note" | "Plan",
  "title": "kratak naslov ili prazno",
  "date_iso": "ISO datetime sa timezone offsetom ili null",
  "project": "projekat ako je jasno ili prazno",
  "reply": "odgovor korisniku ako je action chat ili clarify"
}}

Pravila:
- Ako korisnik razgovara, pita ko si, da li si tu, trazi savet ili objasnjenje: action=chat.
- Ako korisnik zeli da zapamtis ili zabelezis podatak, preferenciju, cinjenicu o sebi ili radni kontekst: action=create_item, type=Note.
- Ako korisnik zeli da podsetis, planiras, zakazes ili napravis obavezu: action=create_item, type=Task ili Plan.
- Ako korisnik trazi podsetnik ali ne kaze na sta tacno da ga podsetis: action=clarify i pitaj kratko sta treba da zapamtis.
- Razumi srpski, latinicu/cirilicu, greske u kucanju i "Ljiljo".
- Bez objasnjenja, analize, thinking process-a i markdowna.
- "stra" u kontekstu podsetnika najverovatnije znaci "sutra".
- Za danas/sutra/petak izracunaj date_iso.
- Ako vreme nije receno, koristi 09:00 za zadatke i planove.

Poruka: {text}
""".strip()
        content = self._chat(
            [
                {"role": "system", "content": "Ti si strogi JSON intent parser. Vracas samo validan JSON."},
                {"role": "user", "content": prompt},
            ],
            timeout=INTENT_TIMEOUT_SECONDS,
            json_mode=True,
            num_predict=INTENT_NUM_PREDICT,
        )
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            raise RuntimeError(f"Ollama nije vratila JSON intent: {content[:200]}")
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Ollama je vratila neispravan JSON intent: {content[:200]}") from exc
        if parsed.get("action") not in {"chat", "create_item", "clarify"}:
            raise RuntimeError(f"Nepoznat intent action: {parsed.get('action')}")
        return parsed

    def link_memory(self, item: dict[str, Any], context: str) -> dict[str, Any]:
        if not context:
            return {"action": "none"}
        prompt = f"""
Poveži novi unos sa postojećom Notion memorijom ako ima dovoljno osnova.

Novi unos:
{json.dumps(item, ensure_ascii=False)}

Notion memorija:
{context}

Vrati ISKLJUCIVO JSON:
{{
  "action": "none" | "link" | "ask",
  "project": "projekat/tema ako je sigurno ili prazno",
  "relation_note": "kratka beleška o vezi ili prazno",
  "question": "kratko pitanje korisniku ako action=ask"
}}

Pravila:
- link samo ako je veza jasna iz teksta ili memorije.
- ask ako postoje 2+ moguće veze ili nedostaje ključna informacija.
- none ako je unos samostalan.
- Ne izmišljaj ljude, firme ni projekte.
""".strip()
        content = self._chat(
            [
                {"role": "system", "content": "Ti si strogi parser za povezivanje memorije. Vracas samo validan JSON."},
                {"role": "user", "content": prompt},
            ],
            timeout=LINK_TIMEOUT_SECONDS,
            json_mode=True,
            num_predict=LINK_NUM_PREDICT,
        )
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            return {"action": "none"}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {"action": "none"}
        if parsed.get("action") not in {"none", "link", "ask"}:
            return {"action": "none"}
        return parsed
