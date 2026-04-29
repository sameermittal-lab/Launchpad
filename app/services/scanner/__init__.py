"""Portal scanner - discovers new job listings from ATS APIs."""

from app.services.scanner.service import scan_all_companies, scan_company

__all__ = ["scan_all_companies", "scan_company"]
