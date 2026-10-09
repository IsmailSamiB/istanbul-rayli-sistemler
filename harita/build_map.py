"""İstasyon kullanım haritasını üretir.

Adımlar:
  1. OpenStreetMap'ten (Overpass API) raylı sistem hatlarının gerçek ray geometrisini indirir
     (data/osm_ham.json; varsa önbellekten okur, --yenile ile yeniden indirir).
  2. Her hat için ilgili OSM rotasının ray parçalarını ayıklar, sadeleştirir ve
     data/hatlar_osm.geojson dosyasına yazar.
  3. data/istasyonlar.json + hat geometrisini (ve varsa build_detay.py çıktısı data/istasyon_detay.json'u) template.html içine gömerek
     istasyon_kullanim_haritasi.html dosyasını oluşturur (tek dosya, sunucu gerekmez).

Kullanım:
  python harita/build_map.py            # önbellekteki OSM verisiyle
  python harita/build_map.py --yenile   # OSM verisini yeniden indir
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

KOK = Path(__file__).resolve().parent
VERI = KOK / "data"
OSM_HAM = VERI / "osm_ham.json"
HATLAR_GEOJSON = VERI / "hatlar_osm.geojson"
ISTASYONLAR = VERI / "istasyonlar.json"
DETAY = VERI / "istasyon_detay.json"   # build_detay.py üretir; yoksa harita yalnızca yıllık toplamla çalışır
SABLON = KOK / "template.html"
CIKTI = KOK / "istasyon_kullanim_haritasi.html"

OVERPASS_SUNUCULARI = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
SORGU = (
    '[out:json][timeout:180];'
    'rel["type"="route"]["route"~"^(subway|light_rail|tram|train|funicular|aerialway)$"]'
    '(40.75,28.55,41.30,29.45);out geom;'
)

# Veri setindeki hat kodu -> kullanılacak OSM rota ilişkileri (relation id).
# Her hattın tek yönü yeterli; dallı hatlarda her dal ayrıca eklenir.
HAT_ROTALARI = {
    "M1": [305496, 4289712],        # M1A Yenikapı→Atatürk Havalimanı, M1B Yenikapı→Kirazlı
    "M2": [11341406, 7719796],      # Yenikapı→Hacıosman, Sanayi→Seyrantepe
    "M3": [4289797],
    "M4": [2396287],
    "M5": [11344904],
    "M6": [7719781],
    "M7": [11799410, 15085834],     # Mecidiyeköy→Mahmutbey, Mecidiyeköy→Yıldız
    "M8": [14900216],
    "M9": [4289800],
    "M11": [15083964],
    "Marmaray": [9987139],
    "SK": [16400029],               # OSM'de T6 Sirkeci–Kazlıçeşme
    "HB": [14039181],               # OSM'de B2 Halkalı–Bahçeşehir
    "T1": [2962729],
    "T2": [301617],
    "T3": [2409338],
    "T4": [11344897],
    "T5": [12174616],
    "F1": [300961],
    "F2": [301616],
    "F4": [14738977],
    "TF1": [9696513],
    "TF2": [20170893],
}

# Sadeleştirme toleransı (derece). ~0.00003° ≈ 3 m; harita ölçeğinde fark edilmez.
TOLERANS = 0.00003


def osm_indir():
    veri = urllib.parse.urlencode({"data": SORGU}).encode()
    for deneme in range(3):
        for url in OVERPASS_SUNUCULARI:
            try:
                istek = urllib.request.Request(url, data=veri, headers={"User-Agent": "istasyon-haritasi/1.0"})
                with urllib.request.urlopen(istek, timeout=240) as yanit:
                    govde = yanit.read()
                json.loads(govde)  # geçerli JSON mu?
                OSM_HAM.write_bytes(govde)
                print(f"OSM verisi indirildi: {url} ({len(govde) / 1e6:.1f} MB)")
                return
            except Exception as hata:  # sunucu meşgul / zaman aşımı
                print(f"  {url} başarısız: {hata}")
        time.sleep(15)
    sys.exit("Overpass sunucularına ulaşılamadı; daha sonra tekrar deneyin.")


def sadelestir(noktalar, tol):
    """Douglas–Peucker çizgi sadeleştirme (lon/lat düzleminde)."""
    if len(noktalar) < 3:
        return noktalar
    (x1, y1), (x2, y2) = noktalar[0], noktalar[-1]
    dx, dy = x2 - x1, y2 - y1
    uzunluk2 = dx * dx + dy * dy
    en_uzak, indeks = 0.0, 0
    for i in range(1, len(noktalar) - 1):
        px, py = noktalar[i]
        if uzunluk2 == 0:
            d = ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
        else:
            d = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / uzunluk2 ** 0.5
        if d > en_uzak:
            en_uzak, indeks = d, i
    if en_uzak <= tol:
        return [noktalar[0], noktalar[-1]]
    return sadelestir(noktalar[: indeks + 1], tol)[:-1] + sadelestir(noktalar[indeks:], tol)


def hat_geometrileri():
    ham = json.loads(OSM_HAM.read_text(encoding="utf-8"))
    iliskiler = {e["id"]: e for e in ham["elements"] if e["type"] == "relation"}
    ozellikler = []
    for hat, rel_idler in HAT_ROTALARI.items():
        gorulen, parcalar = set(), []
        for rid in rel_idler:
            rel = iliskiler.get(rid)
            if rel is None:
                print(f"  UYARI: {hat} için OSM ilişkisi {rid} bulunamadı, düz çizgi kullanılacak")
                continue
            for uye in rel["members"]:
                # Rol boş olanlar ray parçalarıdır; stop/platform üyeleri atlanır.
                if uye["type"] != "way" or uye.get("role") not in ("", "forward", "backward"):
                    continue
                if uye["ref"] in gorulen or "geometry" not in uye:
                    continue
                gorulen.add(uye["ref"])
                nokta = [(g["lon"], g["lat"]) for g in uye["geometry"] if g]
                nokta = sadelestir(nokta, TOLERANS)
                parcalar.append([[round(x, 5), round(y, 5)] for x, y in nokta])
        if parcalar:
            ozellikler.append({
                "type": "Feature",
                "properties": {"line": hat, "osm_relations": rel_idler},
                "geometry": {"type": "MultiLineString", "coordinates": parcalar},
            })
            print(f"  {hat:9s} {len(parcalar):3d} parça, {sum(map(len, parcalar)):5d} nokta")
    geojson = {
        "type": "FeatureCollection",
        "attribution": "© OpenStreetMap katkıcıları, ODbL",
        "features": ozellikler,
    }
    HATLAR_GEOJSON.write_text(json.dumps(geojson, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return geojson


def detay_oku(istasyonlar):
    """istasyon_detay.json'u okur; istasyon sırası ve toplamlar tutmuyorsa gömmez (harita yıllık toplamla çalışır)."""
    if not DETAY.exists():
        return "null"
    detay = json.loads(DETAY.read_text(encoding="utf-8"))
    kayitlar = detay.get("ist", [])
    uyumlu = len(kayitlar) == len(istasyonlar) and all(
        sum(a + b for a, b in k["m"]) == s["pax"] == sum(k["y"]) for k, s in zip(kayitlar, istasyonlar))
    if not uyumlu:
        print("  UYARI: istasyon_detay.json istasyonlar.json ile uyuşmuyor; ay/gün/yaş verisi gömülmedi. "
              "build_detay.py yeniden çalıştırılmalı.")
        return "null"
    return json.dumps(detay, ensure_ascii=False, separators=(",", ":"))


def main():
    if "--yenile" in sys.argv or not OSM_HAM.exists():
        osm_indir()
    print("Hat geometrileri hazırlanıyor…")
    geojson = hat_geometrileri()
    istasyonlar = ISTASYONLAR.read_text(encoding="utf-8")
    html = SABLON.read_text(encoding="utf-8")
    html = html.replace("__DATA__", istasyonlar).replace(
        "__ROUTES__", json.dumps(geojson, ensure_ascii=False, separators=(",", ":"))
    )
    html = html.replace("__DETAIL__", detay_oku(json.loads(istasyonlar)))
    CIKTI.write_text(html, encoding="utf-8")
    print(f"Harita yazıldı: {CIKTI} ({CIKTI.stat().st_size / 1e3:.0f} KB)")


if __name__ == "__main__":
    main()
