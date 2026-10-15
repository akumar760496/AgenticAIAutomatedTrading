"""Entry point so `python -m scripts.verify 0_1` works."""

import sys

from scripts.verify._runner import main

sys.exit(main())
