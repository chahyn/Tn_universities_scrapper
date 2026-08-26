"""
Every source-specific scraper implements this interface.
Keeping a common shape means main.py can run all sources the same way,
and adding a new university source later is just adding a new subclass.
"""
import json
import time
from abc import ABC, abstractmethod

from config import RAW_OUTPUT_DIR, REQUEST_DELAY_SECONDS
from scraper.models import RawUniversity


class BaseScraper(ABC):
    # Override in each subclass with a short, unique, filesystem-safe name
    source_name: str = "base"

    @abstractmethod
    def scrape(self) -> list[RawUniversity]:
        """Return every RawUniversity this source can provide."""
        raise NotImplementedError

    def run(self) -> list[RawUniversity]:
        print(f"[{self.source_name}] starting scrape")
        results = self.scrape()
        print(f"[{self.source_name}] collected {len(results)} universities")
        self._save_raw(results)
        return results

    def _save_raw(self, results: list[RawUniversity]) -> None:
        out_path = RAW_OUTPUT_DIR / f"{self.source_name}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump([r.model_dump() for r in results], f, ensure_ascii=False, indent=2)
        print(f"[{self.source_name}] wrote {out_path}")

    def polite_wait(self) -> None:
        """Call this between page fetches to avoid hammering the source site."""
        time.sleep(REQUEST_DELAY_SECONDS)
