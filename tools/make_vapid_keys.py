"""Run locally to PRINT VAPID keys for GitHub secrets. This script writes no files."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from msac.push import generate_keys


def main():
    generate_keys()


if __name__=='__main__':
    main()
