#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot de surveillance des logements CROUS
---------------------------------------
Surveille trouverunlogement.lescrous.fr pour une zone geographique donnee
et envoie une notification Telegram des qu'un nouveau logement apparait,
avec le lien direct pour postuler.

Auteur : script d'exemple. Usage personnel, a rythme raisonnable.
"""

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests

# ==================================================================
#   >>>>>  REMPLACE JUSTE LES 2 LIGNES CI-DESSOUS  <<<<<
#   (garde bien les guillemets " autour de tes codes)
# ==================================================================

TELEGRAM_TOKEN   = "8943766515:AAG4WYY1mOmVBehYndRhKH7lILjwA8Z-d1Q"     # <-- le code de @BotFather (ex : 8123456789:AAHxyz...)

TELEGRAM_CHAT_ID = "2100878280"   # <-- ton numero de @userinfobot (ex : 123456789)

# ==================================================================
#   Le reste est deja pret, tu n'as rien a changer.
# ==================================================================

# Zone surveillee : toute l'Ile-de-France (deja configuree pour toi).
SEARCH_URL = "https://trouverunlogement.lescrous.fr/tools/47/search?bounds=1.4462445_49.241431_3.5592208_48.1201456&locationName=%C3%8Ele-de-France"

# 4) Intervalle entre deux verifications, en secondes (60 = 1 min).
#    Ne descends pas trop bas pour rester correct avec le serveur.
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))

# Fichier local ou l'on memorise les logements deja vus (evite les doublons).
STATE_FILE = Path("crous_seen.json")

# ============================================================
#  Ne pas toucher en dessous (sauf si tu veux bidouiller)
# ============================================================

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
    """Extrait l'id de l'outil (tool_id) et les coordonnees (bounds) de l'URL."""
    parsed = urlparse(url)

    # tool_id est dans le chemin : /tools/<id>/search
    m = re.search(r"/tools/(\d+)/", parsed.path)
    if not m:
        raise ValueError("Impossible de trouver l'id de l'outil dans l'URL (attendu /tools/<id>/search).")
    tool_id = int(m.group(1))

    # bounds = lon1_lat1_lon2_lat2
    qs = parse_qs(parsed.query)
    if "bounds" not in qs:
        raise ValueError("L'URL ne contient pas de parametre 'bounds'. Refais une recherche par ville sur le site.")
    coords = [float(x) for x in qs["bounds"][0].split("_")]
    if len(coords) != 4:
        raise ValueError("Le parametre 'bounds' doit contenir 4 nombres (lon_lat_lon_lat).")
    lon1, lat1, lon2, lat2 = coords
    return tool_id, lon1, lat1, lon2, lat2


def fetch_accommodations(tool_id, lon1, lat1, lon2, lat2):
    """Interroge l'API CROUS et renvoie la liste brute des logements dispos."""
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

    # La structure peut evoluer : on essaie plusieurs chemins possibles.
    results = data.get("results") or data
    items = results.get("items") or results.get("accommodations") or []
    return items


def describe(item):
    """Fabrique un texte lisible + un lien direct pour un logement."""
    acc_id = item.get("id") or item.get("accommodationId") or "?"
    label = item.get("label") or item.get("title") or "Logement CROUS"

    residence = item.get("residence") or {}
    res_name = residence.get("label") or residence.get("name") or ""

    # Adresse (selon les cas)
    address = ""
    addr_obj = residence.get("address") or item.get("address") or {}
    if isinstance(addr_obj, dict):
        address = " ".join(
            str(addr_obj.get(k, "")) for k in ("line1", "line2", "city", "zipCode")
        ).strip()
    elif isinstance(addr_obj, str):
        address = addr_obj

    # Prix (selon les cas)
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
    """Envoie un message sur Telegram."""
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
        # Telegram explique precisement pourquoi ca a echoue :
        print(f"[!] Telegram a refuse : {data.get('description')}")
        return False
    except Exception as e:
        print(f"[!] Erreur reseau Telegram : {e}")
        return False


def load_seen():
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    STATE_FILE.write_text(json.dumps(sorted(str(s) for s in seen)))


# Variable globale pratique pour construire les liens
TOOL_ID, LON1, LAT1, LON2, LAT2 = parse_search_url(SEARCH_URL)


def main():
    print("=== Bot CROUS demarre ===")

    # -- Verification des codes Telegram --
    if "ICI_TON" in TELEGRAM_TOKEN or "ICI_TON" in TELEGRAM_CHAT_ID:
        print("[!] Tu n'as pas remplace TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID en haut du fichier.")
        return

    # -- Test d'envoi immediat pour valider la config --
    print("-> Test d'envoi d'un message Telegram...")
    if send_telegram("🔔 Test : ton bot CROUS est bien connecte !"):
        print("-> OK ! Regarde Telegram, tu dois avoir recu le message de test.\n")
    else:
        print("-> Echec. Corrige le token ou le chat_id ci-dessus, puis relance.\n")
        return

    print(f"Zone (tool {TOOL_ID}) : {LON1},{LAT1} -> {LON2},{LAT2}")
    print(f"Verification toutes les {CHECK_INTERVAL} s. Ctrl+C pour arreter.\n")

    seen = load_seen()
    first_run = len(seen) == 0

    while True:
        try:
            items = fetch_accommodations(TOOL_ID, LON1, LAT1, LON2, LAT2)
            current = {str(it.get("id") or it.get("accommodationId")): it for it in items}
            new_ids = [i for i in current if i not in seen]

            if first_run:
                # Premier passage : on note ce qui existe deja sans spammer.
                send_telegram(
                    f"✅ Bot CROUS actif.\n{len(current)} logement(s) actuellement dans ta zone. "
                    f"Je te previens des qu'un nouveau apparait."
                )
                seen.update(current.keys())
                save_seen(seen)
                first_run = False
            elif new_ids:
                print(f"[+] {len(new_ids)} nouveau(x) logement(s) !")
                for i in new_ids:
                    acc_id, msg = describe(current[i])
                    send_telegram(f"🏠 <b>Nouveau logement CROUS !</b>\n\n{msg}")
                    seen.add(str(acc_id))
                save_seen(seen)
            else:
                print(f"[{time.strftime('%H:%M:%S')}] Rien de nouveau ({len(current)} dispo).")

        except requests.HTTPError as e:
            print(f"[!] Erreur HTTP : {e} (le serveur CROUS a peut-etre change son API)")
        except Exception as e:
            print(f"[!] Erreur : {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
