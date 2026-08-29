from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import data as data_module

MIN_BARS = 1500

CANDIDATES = """
ABBOTINDIA ACC ADANIENSOL ADANIGREEN ADANIPOWER AARTIIND AAVAS ABCAPITAL
ABFRL AEGISLOG AFFLE AJANTPHARM ALKEM ALKYLAMINE AMBUJACEM ANGELONE APLAPOLLO
APOLLOTYRE ASAHIINDIA ASTERDM ATUL AUROPHARMA AVANTIFEED BALRAMCHIN BANDHANBNK
BATAINDIA BAYERCROP BDL BEML BIRLACORPN BLUEDART BRIGADE BSE CAMS CANFINHOME
CAPLIPOINT CARBORUNIV CASTROLIND CCL CDSL CEATLTD CENTURYPLY CERA CESC CHALET
CHAMBLFERT CHENNPETRO CIEINDIA CLEAN COFORGE COLPAL COROMANDEL CRAFTSMAN
CREDITACC CRISIL CROMPTON CUB CUMMINSIND CYIENT DALBHARAT DATAPATTNS DEEPAKNTR
DELHIVERY DEVYANI DIXON DLF DMART EIDPARRY EIHOTEL ELGIEQUIP EMAMILTD ENDURANCE
ENGINERSIN EQUITASBNK ERIS EXIDEIND FDC FINCABLES FINEORG FIVESTAR FLUOROCHEM
FORTIS FSL GARFIBRES GESHIP GILLETTE GLAND GLAXO GNFC GODREJAGRO GODREJCP
GODREJIND GRANULES GRAPHITE GRINDWELL GSFC GSPL GUJGASLTD HAPPSTMNDS HATSUN
HBLPOWER HEG HEIDELBERG HFCL HIKAL HINDCOPPER HONAUT IDFCFIRSTB IEX IIFL
INDIACEM INDIAMART INDIGO INDOCO INTELLECT IPCALAB IRCTC IRFC ISEC ITI
JBCHEPHARM JKCEMENT JKLAKSHMI JKPAPER JKTYRE JMFINANCIL JSL JUSTDIAL JYOTHYLAB
KAJARIACER KANSAINER KARURVYSYA KEC KFINTECH KIMS KIRLOSENG KNRCON KPITTECH
KPRMILL KRBL LATENTVIEW LAURUSLABS LEMONTREE LICHSGFIN LINDEINDIA LTIM LUXIND
MAHABANK MAHLIFE MANAPPURAM MARICO MASTEK MAXHEALTH MAZDOCK MEDANTA METROBRAND
MGL MINDACORP MRPL MSUMI NATCOPHARM NAVINFLUOR NBCC NCC NESCO NETWORK18 NH
NIACL NLCINDIA NOCIL NUVOCO OBEROIRLTY OIL ORIENTELEC PATANJALI PAYTM PCBL PEL
PFIZER PGHH PHOENIXLTD PIIND PNBHOUSING POLICYBZR POLYCAB POONAWALLA PRAJIND
PRINCEPIPE PVRINOX QUESS RADICO RAILTEL RAINBOW RAJESHEXPO RALLIS RATNAMANI
RAYMOND RBLBANK RCF REDINGTON RELAXO RHIM ROUTE RVNL SANOFI SAPPHIRE SCHAEFFLER
SHARDACROP SHOPERSTOP SHYAMMETL SIEMENS SJVN SKFINDIA SOBHA SONACOMS SPARC
STARHEALTH SUMICHEM SUNDARMFIN SUNDRMFAST SUPRAJIT SURYAROSNI SWANENERGY
SYMPHONY TANLA TATACHEM TATACOMM TATATECH TCI TEAMLEASE TEGA THERMAX TIINDIA
TIMKEN TITAGARH TORNTPOWER TRIDENT TRITURBINE TTKPRESTIG UJJIVANSFB UNOMINDA
USHAMART UTIAMC VAIBHAVGBL VARROC VBL VGUARD VINATIORGA VIPIND VSTIND WELCORP
WELSPUNLIV WESTLIFE WHIRLPOOL ZENSARTECH ZFCVINDIA ZYDUSLIFE
""".split()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-bars", type=int, default=MIN_BARS)
    parser.add_argument("--pause", type=float, default=0.3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    existing = {s.upper() for s in config.UNIVERSE}
    todo = [s for s in dict.fromkeys(CANDIDATES) if s.upper() not in existing]
    print(f"universe now {len(existing)} | {len(todo)} candidates to try",
          flush=True)

    if not args.dry_run and os.path.exists(config.STOCKS_PATH):
        shutil.copyfile(config.STOCKS_PATH,
                        config.STOCKS_PATH + ".before-expand")
        print(f"backed up {config.STOCKS_PATH}", flush=True)

    added = short = failed = 0
    for index, symbol in enumerate(todo, start=1):
        try:
            frame = data_module.fetch(symbol, quiet=True)
            bars = len(frame)
        except Exception as error:                              # noqa: BLE001
            failed += 1
            print(f"[{index:>3}/{len(todo)}] {symbol:<14} fetch failed "
                  f"({type(error).__name__})", flush=True)
            time.sleep(args.pause)
            continue

        if bars < args.min_bars:
            short += 1
            print(f"[{index:>3}/{len(todo)}] {symbol:<14} only {bars} bars, "
                  f"skipped", flush=True)
            time.sleep(args.pause)
            continue

        if args.dry_run:
            added += 1
            print(f"[{index:>3}/{len(todo)}] {symbol:<14} {bars} bars, would "
                  f"add", flush=True)
        else:
            ok, message = config.add_stock(symbol)
            added += 1 if ok else 0
            print(f"[{index:>3}/{len(todo)}] {symbol:<14} {bars} bars -> "
                  f"{message}", flush=True)
        time.sleep(args.pause)

    print(f"\nadded {added} | too short {short} | failed {failed}", flush=True)
    if not args.dry_run:
        import importlib
        importlib.reload(config)
        print(f"universe is now {len(config.UNIVERSE)} names", flush=True)


if __name__ == "__main__":
    main()
