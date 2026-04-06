#!/usr/bin/env python3
"""Geo-Moderation Tester — standalone script."""

import sys, os, json, time, requests, statistics
from datetime import datetime

API_KEY = "5ad68274-f23b-4b67-9fa2-12baae1ea11d"
GROK_URL = "https://grok2api-beryl-sigma.vercel.app"
ADMIN_KEY = "Tia*2019"
PUBLIC_KEY = "Tia*2019"
SUB_USER_ID = 1647
IMAGES_PER_COUNTRY = 4
OUTPUT_DIR = "/home/user/grok2api/geo_test_results"
MIN_NORMAL_SIZE = 50_000
PASS_THRESHOLD = 2

COUNTRIES = [
    "us", "pe", "pa", "bo", "la", "br", "ar", "mx", "co", "cl",
    "jp", "kr", "th", "vn", "in", "gb", "de", "fr", "it", "es",
    "nl", "se", "no", "au", "nz", "ca", "id", "my", "tr", "ru",
    "sa", "ae", "qa", "ph", "sg", "za", "ng", "ke", "eg", "il",
]


def get_proxy(country_code):
    try:
        r = requests.get(
            f"https://api.ipoasis.com/v1/proxy/dynamic/{SUB_USER_ID}",
            params={"count": 1, "country": country_code, "protocol": "http", "sessionType": "sticky"},
            headers={"X-API-KEY": API_KEY},
            timeout=10,
        )
        urls = r.json()
        return urls[0] if urls else None
    except Exception as e:
        print(f"    Proxy error: {e}")
        return None


def set_proxy(proxy_url):
    try:
        requests.post(
            f"{GROK_URL}/v1/admin/config",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {ADMIN_KEY}"},
            json={"proxy": {"base_proxy_url": proxy_url, "asset_proxy_url": proxy_url}},
            timeout=10,
        )
    except Exception as e:
        print(f"    Set proxy error: {e}")


def clear_proxy():
    set_proxy("")


def generate_image(prompt):
    try:
        r = requests.post(
            f"{GROK_URL}/v1/images/generations",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {PUBLIC_KEY}"},
            json={"prompt": prompt, "model": "grok-imagine-1.0", "n": 1, "nsfw": True},
            timeout=120,
        )
        return r.json()
    except Exception as e:
        return {"error": {"message": str(e)}}


def download_image(url, filepath):
    try:
        r = requests.get(url, timeout=30, stream=True)
        if r.status_code == 200:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            return os.path.getsize(filepath)
    except Exception as e:
        print(f"    Download error: {e}")
    return 0


def get_dims(filepath):
    try:
        from PIL import Image
        img = Image.open(filepath)
        return img.size
    except Exception:
        return (0, 0)


def test_country(country, prompt, run_dir):
    cc_dir = os.path.join(run_dir, country)
    os.makedirs(cc_dir, exist_ok=True)

    proxy_url = get_proxy(country)
    if not proxy_url:
        print(f"    NO PROXY")
        return {"country": country, "status": "NO_PROXY", "score": 0, "pass": False,
                "total": IMAGES_PER_COUNTRY, "ok_count": 0, "pass_count": 0,
                "error_count": 0, "avg_size": 0, "images": []}

    set_proxy(proxy_url)
    time.sleep(0.5)

    images = []
    for i in range(IMAGES_PER_COUNTRY):
        print(f"    Image {i+1}/{IMAGES_PER_COUNTRY}...", end=" ", flush=True)
        resp = generate_image(prompt)

        if "error" in resp:
            msg = resp.get("error", {}).get("message", "unknown")[:100]
            print(f"ERROR: {msg}")
            images.append({"status": "error", "error": msg, "size": 0})
            continue

        img_url = None
        data = resp.get("data", [])
        if isinstance(data, list) and data:
            item = data[0]
            if isinstance(item, dict):
                img_url = item.get("url") or item.get("b64_json")
            elif isinstance(item, str) and item.startswith("http"):
                img_url = item

        if not img_url:
            print("NO_URL")
            images.append({"status": "no_url", "size": 0})
            continue

        filepath = os.path.join(cc_dir, f"{country}_{i+1}.jpg")
        fsize = download_image(img_url, filepath)
        w, h = get_dims(filepath) if fsize > 0 else (0, 0)
        verdict = "PASS" if fsize > MIN_NORMAL_SIZE else "SMALL"
        print(f"{fsize:,} bytes {w}x{h} [{verdict}]")
        images.append({"status": "ok", "size": fsize, "width": w, "height": h, "file": filepath})
        time.sleep(0.3)

    clear_proxy()

    ok_imgs = [img for img in images if img["status"] == "ok" and img["size"] > 0]
    sizes = [img["size"] for img in ok_imgs]
    pass_count = sum(1 for img in images if img["status"] == "ok" and img["size"] > MIN_NORMAL_SIZE)
    score = sum(1.0 if img.get("size", 0) > MIN_NORMAL_SIZE else (0.2 if img.get("size", 0) > 0 else 0.0) for img in images)
    score = round(score / max(IMAGES_PER_COUNTRY, 1), 2)

    return {
        "country": country, "status": "OK", "score": score,
        "pass": pass_count >= PASS_THRESHOLD,
        "total": IMAGES_PER_COUNTRY, "ok_count": len(ok_imgs),
        "pass_count": pass_count,
        "error_count": sum(1 for img in images if img["status"] == "error"),
        "avg_size": int(statistics.mean(sizes)) if sizes else 0,
        "images": images,
    }


def main():
    if len(sys.argv) < 2:
        print('Usage: python3 geo_test_run.py "your prompt here"')
        sys.exit(1)

    prompt = sys.argv[1]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(OUTPUT_DIR, timestamp)
    os.makedirs(run_dir, exist_ok=True)

    print(f"=== Geo-Moderation Tester ===")
    print(f"Prompt: {prompt[:80]}")
    print(f"Countries: {len(COUNTRIES)}")
    print(f"Images/country: {IMAGES_PER_COUNTRY}")
    print(f"Output: {run_dir}")
    print()

    results = []
    for i, cc in enumerate(COUNTRIES):
        print(f"[{i+1}/{len(COUNTRIES)}] {cc.upper()}")
        r = test_country(cc, prompt, run_dir)
        results.append(r)
        icon = "PASS" if r.get("pass") else ("SKIP" if r["status"] == "NO_PROXY" else "FAIL")
        print(f"    => {icon} score={r['score']} pass_images={r['pass_count']}/{IMAGES_PER_COUNTRY} avg={r['avg_size']:,}B")
        print()

    clear_proxy()

    with open(os.path.join(run_dir, "results.json"), "w") as f:
        json.dump(results, f, indent=2)

    ranked = sorted(results, key=lambda x: (-x.get("score", 0), -x.get("avg_size", 0)))
    print("=" * 60)
    print("RANKED (most permissive -> most restrictive)")
    print("=" * 60)
    print(f"{'#':<4} {'CC':<5} {'Score':<7} {'Pass':<8} {'OK/Tot':<8} {'Avg Size':<12} Result")
    print("-" * 60)
    for i, r in enumerate(ranked, 1):
        cc = r["country"].upper()
        ok = r.get("ok_count", 0)
        pc = r.get("pass_count", 0)
        t = r.get("total", IMAGES_PER_COUNTRY)
        avg = r.get("avg_size", 0)
        icon = "PASS" if r.get("pass") else ("SKIP" if r["status"] == "NO_PROXY" else "FAIL")
        print(f"{i:<4} {cc:<5} {r['score']:<7} {pc}/{t:<6} {ok}/{t:<6} {avg:>10,} B  {icon}")

    print(f"\nResults: {run_dir}/results.json")
    print(f"Images:  {run_dir}/<country>/")


if __name__ == "__main__":
    main()
