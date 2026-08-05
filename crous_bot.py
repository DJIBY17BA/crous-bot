#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot de surveillance des logements CROUS  --  version GitHub Actions.

Ce script s'execute UNE fois a chaque declenchement (toutes les ~5 min via
GitHub). Il compare la liste des logements avec l'etat precedent et notifie
sur Telegram uniquement les NOUVEAUX logements.

Le token et le chat_id ne sont PAS dans ce fichier : ils sont fournis par
GitHub via des "secrets" (variables d'environnement). Ne mets jamais ton
token en clair dans le code.
"""

import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests

# --- Codes lus depuis les secrets GitHub (a configurer dans le depot) ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# --- Zone surveillee : toute l'Ile-de-France (deja configuree) ---
SEARCH_URL = "https://trouverunlogement.lescrous.fr/tools/47/search?bounds=1.4462445_49.241431_3.5592208_48.1201456&locationName=%C3%8Ele-de-France"

STATE_FILE = Path("crous_seen.json")
BASE = "https://trouverunlogement.lescrous.fr"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": BASE,
    "Referer": SEARCH_URL,
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}


def parse_search_url(url):
    """Extrait l'id de l'outil et les coordonnees de la zone depuis l'URL."""
    parsed = urlparse(url)
    m = re.search(r"/tools/(\d+)/", parsed.path)
    if not m:
        raise ValueError("Id de l'outil introuvable dans l'URL.")
    tool_id = int(m.group(1))
    qs = parse_qs(parsed.query)
    coords = [float(x) for x in qs["bounds"][0].split("_")]
    lon1, lat1, lon2, lat2 = coords
    return tool_id, lon1, lat1, lon2, lat2


def fetch_accommodations(tool_id, lon1, lat1, lon2, lat2):
    """Interroge l'API CROUS et renvoie la liste des logements dispos."""
    api_url = f"{BASE}/api/fr/search/{tool_id}"
    body = {
        "idTool": tool_id,
        "need_aggregation": False,
        "page": 1,
        "pageSize": 100,
        "sector": None,
        "occupationModes": [],
        "location": [
            {"lon": lon1, "lat": lat1},
            {"lon": lon2, "lat": lat2},
        ],
        "residence": None,
        "precision": 5,
        "equipment": [],
        "price": {"min": 0, "max": 100000},
    }
    resp = requests.post(api_url, headers=HEADERS, json=body, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results") or data
    return results.get("items") or results.get("accommodations") or []


def describe(item):
    """Fabrique un texte lisible + un lien direct pour un logement."""
    acc_id = item.get("id") or item.get("accommodationId") or "?"
    label = item.get("label") or item.get("title") or "Logement CROUS"

    residence = item.get("residence") or {}
    res_name = residence.get("label") or residence.get("name") or ""

    address = ""
    addr_obj = residence.get("address") or item.get("address") or {}
    if isinstance(addr_obj, dict):
        address = " ".join(
            str(addr_obj.get(k, "")) for k in ("line1", "line2", "city", "zipCode")
        ).strip()
    elif isinstance(addr_obj, str):
        address = addr_obj

    price = item.get("price")
    if isinstance(price, dict):
        price = price.get("min") or price.get("value")
    price_txt = f" — {price} €" if price else ""

    link = f"{BASE}/tools/{TOOL_ID}/accommodations/{acc_id}"

    parts = [f"<b>{label}</b>{price_txt}"]
    if res_name:
        parts.append(res_name)
    if address:
        parts.append(address)
    parts.append(link)
    return acc_id, "\n".join(parts)


def send_telegram(text):
    """Envoie un message sur Telegram. Renvoie True si OK."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, data=payload, timeout=30)
        data = r.json()
        if data.get("ok"):
            return True
        print(f"[!] Telegram a refuse : {data.get('description')}")
        return False
    except Exception as e:
        print(f"[!] Erreur reseau Telegram : {e}")
        return False


def load_state():
    """Renvoie (deja_initialise, ensemble_des_ids_deja_vus)."""
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            return bool(data.get("init")), set(data.get("seen", []))
        except Exception:
            return False, set()
    return False, set()


def save_state(seen):
    STATE_FILE.write_text(
        json.dumps({"init": True, "seen": sorted(str(s) for s in seen)}, ensure_ascii=False)
    )


TOOL_ID, LON1, LAT1, LON2, LAT2 = parse_search_url(SEARCH_URL)


def main():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[!] Secrets TELEGRAM_TOKEN / TELEGRAM_CHAT_ID manquants.")
        raise SystemExit(1)

    initialized, seen = load_state()
    items = fetch_accommodations(TOOL_ID, LON1, LAT1, LON2, LAT2)
    current = {str(it.get("id") or it.get("accommodationId")): it for it in items}

    # Tout premier passage : on note l'existant sans spammer, et on confirme.
    if not initialized:
        send_telegram(
            "✅ Surveillance CROUS activee (Ile-de-France).\n"
            f"{len(current)} logement(s) actuellement. "
            "Je te previens des qu'un nouveau apparait, meme PC eteint."
        )
        save_state(set(current.keys()))
        print(f"Initialisation OK ({len(current)} logement(s)).")
        return

    # Passages suivants : on notifie les nouveaux logements.
    new_ids = [i for i in current if i not in seen]
    if new_ids:
        print(f"{len(new_ids)} nouveau(x) logement(s) !")
        for i in new_ids:
            _, msg = describe(current[i])
            send_telegram(f"🏠 <b>Nouveau logement CROUS !</b>\n\n{msg}")
    else:
        print(f"Rien de nouveau ({len(current)} dispo).")

    seen |= set(current.keys())
    save_state(seen)


if __name__ == "__main__":
    main()
