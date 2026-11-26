"""OLDCC Defense Spending by State: the "online data" workbook (contracts, grants, payroll, personnel by state).

The workbook URL carries its upload month, so the landing page of the newest fiscal year is read for the .xlsx
link; the FY2024 file found by the research is the fallback.
"""

from __future__ import annotations

import contextlib
import re
import ssl
import urllib.request
from datetime import date

from ..core.source import Source, SourceInfo
from ._resource_util import download_checked, num, xlsx_records

SITE = "https://oldcc.gov"
PAGE = SITE + "/defense-spending-state-fiscal-year-{fy}"
FALLBACK = {2024: SITE + "/sites/default/files/2025-11/oldcc_spending_by_state_fy2024_online_data_final_abridged_nov2025.xlsx"}
# oldcc.gov serves only its leaf certificate, without the DigiCert intermediate, which Python (no AIA fetching)
# then rejects. The intermediate the leaf names (its AIA "CA Issuers" URL) is fetched from DigiCert over verified
# HTTPS and added for oldcc.gov requests only; partial-chain trust stays off, so the chain must still end at a
# root in the system store. Verification is never disabled.
INTERMEDIATE = "https://cacerts.digicert.com/DigiCertGlobalG2TLSRSASHA2562020CA1-1.crt"
XLSX_LINK = re.compile(r'href="([^"]+?\.xlsx)"', re.I)

SUMMARY = "15.All Stats, FY{fy}"
COUNTIES = "2.Contract Spending by Cnty"
VENDORS = "3.Top Defense Vendors"


class OLDCCSpending(Source):
    info = SourceInfo(
        id="oldcc_spending",
        title="Defense Spending by State (online data workbook)",
        publisher="U.S. Department of Defense, Office of Local Defense Community Cooperation (OLDCC)",
        homepage="https://oldcc.gov/defense-spending-state-fiscal-year-2024",
        license="U.S. Government work, public",
        description="DoD spending in each state for one fiscal year: contract obligations (by place of performance), "
        "grants, and payroll of civilians, active-duty members and Guard/Reserve, with the total as a share of state "
        "GDP and per resident, plus the top 10 counties by contract spending and the top 10 contractors in each state. "
        "Contract dollars follow where the work is performed (e.g. Northern Virginia, Huntsville AL, Connecticut "
        "shipyards), not where bases are.",
        used_for=["Military advisor: defense dollars by state", "Top defense-contract counties"],
    )

    def _cached(self):
        files = sorted(self.raw_dir.glob("fy*/spending.xlsx"), reverse=True)
        return files[0].relative_to(self.raw_dir).as_posix() if files else None

    @contextlib.contextmanager
    def _tls(self):
        """urllib's global opener with the missing intermediate added (restored afterwards)."""
        der = self.download(INTERMEDIATE, "tls/digicert_g2_tls_rsa_sha256_2020_ca1.crt", expect_binary=True).read_bytes()
        ctx = ssl.create_default_context()
        ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
        ctx.load_verify_locations(cadata=der)
        saved = urllib.request._opener  # noqa: SLF001 - restore whatever was installed
        urllib.request.install_opener(urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)))
        try:
            yield
        finally:
            urllib.request._opener = saved  # noqa: SLF001

    def fetch(self, force=False):
        if self.offline:
            if not self._cached():
                raise FileNotFoundError("oldcc_spending: no cached workbook (--offline)")
            return
        with self._tls():
            self._fetch(force)

    def _fetch(self, force):
        for fy in range(date.today().year, date.today().year - 4, -1):
            page = download_checked(self, PAGE.format(fy=fy), f"fy{fy}/page.html", force=True, skip_errors=True)
            links = []
            if page:
                html = page.read_text(errors="replace")
                links = [l for l in XLSX_LINK.findall(html) if f"fy{fy}" in l.lower() or str(fy) in l]
                links.sort(key=lambda l: ("online" not in l.lower(), l))
            if not links and fy in FALLBACK:
                links = [FALLBACK[fy]]
            for link in links:
                url = link if link.startswith("http") else SITE + link
                if download_checked(self, url, f"fy{fy}/spending.xlsx", force=force, magic=b"PK", binary=True,
                                    skip_errors=True):
                    print(f"    OLDCC FY{fy}: {url}")
                    return
        raise RuntimeError("no OLDCC Defense Spending by State workbook found")

    def process(self):
        rel = self._cached()
        if not rel:
            raise FileNotFoundError("oldcc_spending: run fetch first")
        fy = int(re.search(r"fy(\d{4})", rel).group(1))
        path = self.raw_dir / rel
        states, total = {}, None
        for r in xlsx_records(path, "Spending in State", SUMMARY.format(fy=fy)):
            name = r.get("State", "").strip()
            if not name:
                continue
            rec = {
                "contracts": num(r.get("Contract Spending")),
                "grants": num(r.get("Grant Spending")),
                "civilian_pay": num(r.get("Civilian Pay")),
                "active_duty_pay": num(r.get("Military Active Duty Pay")),
                "guard_reserve_pay": num(r.get("National Guard & Reserve Pay")),
                "payroll": num(r.get("Ttl. Payroll")),
                "total": num(r.get("Spending in State")),
                "pct_gdp": num(r.get("Pct. of State GDP")),
                "per_resident": num(r.get("Spending per Resident")),
                "personnel": num(r.get("Ttl. Personnel")),
            }
            if name.lower().startswith("50 states"):
                total = rec
            else:
                states[name] = rec
        counties: dict[str, list] = {}
        for r in xlsx_records(path, "County", COUNTIES):
            st, cn, v = r.get("State", "").strip(), r.get("County", "").strip(), num(r.get(f"Spending FY{fy}"))
            if st and cn:
                counties.setdefault(st, []).append({"county": cn, "contracts": v})
        vendors: dict[str, list] = {}
        for r in xlsx_records(path, "Contractor", VENDORS):
            st, name, v = r.get("State", "").strip(), r.get("Contractor", "").strip(), num(r.get(f"Spending FY{fy}"))
            if st and name:
                vendors.setdefault(st, []).append({"name": name, "contracts": v})
        if len(states) < 50:
            raise RuntimeError(f"OLDCC: only {len(states)} states parsed")
        print(f"    OLDCC FY{fy}: {len(states)} states, total ${total['total'] / 1e9:,.1f}B" if total else
              f"    OLDCC FY{fy}: {len(states)} states")
        return {"vintage": f"OLDCC FY{fy}", "_files": [rel], "fiscal_year": fy, "states": states, "total": total,
                "counties": counties, "vendors": vendors}
