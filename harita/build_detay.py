"""Haritadaki her istasyon için ay, gün türü ve yaş grubu özetini üretir.

Girdi : passenger_clean.csv (clean_data.py çıktısı; proje kökünde, repoda değil)
        harita/data/istasyonlar.json
Çıktı : harita/data/istasyon_detay.json  → build_map.py bunu haritaya gömer

Kullanım (proje kökünden, pandas kurulu ortamla):
  python harita/build_detay.py

Eşleştirme: CSV'deki giriş adları analiz.ipynb'deki istasyon_adi() ile istasyon adına indirgenir,
sonra haritadaki istasyonla (hat, yıllık toplam yolcu) üzerinden eşlenir. Haritada birleştirilmiş
çift kayıtlar ad önekiyle toplanır. Eşleşmeyen harita istasyonu kalırsa script durur.
"""
import json
import re
import sys
from pathlib import Path

import pandas as pd

KOK = Path(__file__).resolve().parent
CSV = KOK.parent / "passenger_clean.csv"
ISTASYONLAR = KOK / "data" / "istasyonlar.json"
CIKTI = KOK / "data" / "istasyon_detay.json"

YAS_SIRASI = ["<20", "20-30", "30-60", "60+", "Bilinmiyor"]
# Haritadaki kısa hat kodu -> CSV'deki line_code
HAT_CSV = {"F2": "IETT TUNEL", "T2": "IETT NOSTALJIK TRAMVAY",
           "SK": "TCDD SIRKECI-KAZLICESME", "HB": "TCDD - HALKALI-BASAKSEHIR"}


def tr_kucuk(metin):
    return metin.replace("I", "ı").replace("İ", "i").lower()


def tr_baslik(metin):
    return " ".join({"i": "İ", "ı": "I"}.get(k[0], k[0].upper()) + k[1:] for k in metin.split())


def istasyon_adi(ad):
    ad = tr_kucuk(ad)
    ad = re.sub(r"\(.*?\)", " ", ad)
    ad = re.sub(r"\b(m\d+|hol|konkors)\b", " ", ad)
    ad = re.sub(r"\b(kuzey|güney|guney|doğu|dogu|batı|bati)\b", " ", ad)
    ad = re.sub(r"-\s*\d+$", " ", ad)
    ad = re.sub(r"(?<=\D)\s\d+(\s|$)", " ", ad)
    ad = re.sub(r"\s+", " ", ad).strip()
    ad = {"ayrılıkçeşmesi": "ayrılıkçeşme", "özgürlük meydan": "özgürlük meydanı"}.get(ad, ad)
    return tr_baslik(ad).replace("İtü", "İTÜ")


def main():
    if not CSV.exists():
        sys.exit(f"{CSV.name} bulunamadı. Önce clean_data.py çalıştırılmalı.")
    df = pd.read_csv(CSV, sep=";", encoding="utf-8-sig", parse_dates=["date"],
                     usecols=["date", "transaction_month", "line_code", "station_name", "age", "is_weekend", "passanger_cnt"])
    df["ist"] = df["station_name"].map(istasyon_adi)
    df["anahtar"] = list(zip(df["line_code"], df["ist"]))
    yillik = df.groupby("anahtar")["passanger_cnt"].sum()

    istasyonlar = json.loads(ISTASYONLAR.read_text(encoding="utf-8"))
    # (hat, yıllık toplam) -> CSV anahtarları
    toplamdan = {}
    for (hat, ist), v in yillik.items():
        toplamdan.setdefault((hat, int(v)), []).append((hat, ist))

    eslesme, kullanilan = [], set()
    for s in istasyonlar:
        hat = HAT_CSV.get(s["line"], s["line"])
        adaylar = [a for a in toplamdan.get((hat, s["pax"]), []) if a not in kullanilan]
        if len(adaylar) == 1:
            grup = adaylar
        else:  # haritada birleştirilmiş kayıtlar: adı aynı kökle başlayan CSV grupları
            kok = tr_kucuk(s["name"])
            grup = [k for k in yillik.index if k[0] == hat and k not in kullanilan
                    and (kok in tr_kucuk(k[1]) or tr_kucuk(k[1]) in kok)]
            if sum(int(yillik[k]) for k in grup) != s["pax"]:
                sys.exit(f"Eşleşmedi: {s['line']} {s['name']} ({s['pax']}) -> adaylar {grup}")
        kullanilan.update(grup)
        eslesme.append(grup)

    artan = [k for k in yillik.index if k not in kullanilan]
    if artan:
        print("Haritada karşılığı olmayan CSV grupları (bilgi):",
              [(k, int(yillik[k])) for k in artan])

    gunler = df.drop_duplicates("date").groupby(["transaction_month", "is_weekend"])["date"].nunique().unstack(fill_value=0)
    gunler = gunler.reindex(index=range(1, 13), columns=[False, True], fill_value=0)   # eksik ay olsa da 12 değer
    gun = {"hi": [int(gunler.loc[a, False]) for a in range(1, 13)],
           "hs": [int(gunler.loc[a, True]) for a in range(1, 13)]}

    aylik = df.groupby(["anahtar", "transaction_month", "is_weekend"])["passanger_cnt"].sum()
    yas = df.groupby(["anahtar", "age"])["passanger_cnt"].sum()

    kayitlar = []
    for s, grup in zip(istasyonlar, eslesme):
        m = [[0, 0] for _ in range(12)]
        y = [0] * len(YAS_SIRASI)
        for k in grup:
            for (ay, hs), v in aylik[k].items():
                m[ay - 1][1 if hs else 0] += int(v)
            for grp, v in yas[k].items():
                y[YAS_SIRASI.index(grp)] += int(v)
        if not sum(a + b for a, b in m) == s["pax"] == sum(y):
            sys.exit(f"Toplam tutmuyor: {s['line']} {s['name']}")
        kayitlar.append({"m": m, "y": y})

    cikti = {"yas": YAS_SIRASI, "gun": gun, "ist": kayitlar}
    CIKTI.write_text(json.dumps(cikti, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(kayitlar)}/{len(istasyonlar)} istasyon eşleşti; "
          f"toplam {sum(s['pax'] for s in istasyonlar):,} yolcu; "
          f"gün sayısı hafta içi {sum(gun['hi'])}, hafta sonu {sum(gun['hs'])}")
    print(f"Yazıldı: {CIKTI} ({CIKTI.stat().st_size / 1e3:.0f} KB)")


if __name__ == "__main__":
    main()
