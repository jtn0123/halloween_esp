"""tools/notices_model.py — the licence policy a shipped component is held to.

The GPL test the production plan asks for lives here: a copyleft licence in
anything redistributed is an error unless the table records why it ships,
and a non-commercial licence is an error whatever the table says. The
expression parser and the copyright-line reader are tested here too,
because the policy is only as good as what they hand it.

So is the project's OWN licence (docs/LICENSING.md, decision 1): LICENSE
is the MIT License, every manifest of ours names it, and the notices say
what it covers — this project's work, not the firmware image as a whole.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import notices_desktop as nd
import notices_model as nm
import notices_render as nr
from notices_model import Component, SpdxError

OK = ("Copyright (c) 2026 Somebody",)


def comp(license_: str, **kw: object) -> Component:
    fields: dict = {"copyright": OK, **kw}
    return Component("thing", "1.0", license_, **fields)


class SpdxTests(unittest.TestCase):
    def test_or_and_slash_are_alternatives(self) -> None:
        for expr in ("MIT OR Apache-2.0", "MIT/Apache-2.0", "mit or Apache-2.0"):
            with self.subTest(expr=expr):
                alts = nm.alternatives(expr)
                self.assertEqual(len(alts), 2)
                self.assertIn(("Apache-2.0",), alts)

    def test_and_distributes_over_or(self) -> None:
        self.assertEqual(
            nm.alternatives("(MIT OR Apache-2.0) AND Unicode-3.0"),
            [("MIT", "Unicode-3.0"), ("Apache-2.0", "Unicode-3.0")],
        )

    def test_with_stays_attached_to_its_licence(self) -> None:
        self.assertEqual(
            nm.alternatives("Apache-2.0 WITH LLVM-exception OR MIT"),
            [("Apache-2.0 WITH LLVM-exception",), ("MIT",)],
        )

    def test_unreadable_expressions_are_refused(self) -> None:
        for bad in ("", "MIT AND", "(MIT", "MIT)", "MIT $", "AND MIT", "MIT WITH"):
            with self.subTest(expr=bad), self.assertRaises(SpdxError):
                nm.alternatives(bad)

    def test_the_cheapest_alternative_is_the_one_complied_with(self) -> None:
        self.assertEqual(nm.chosen("Apache-2.0 OR MIT"), ("MIT",))
        self.assertEqual(nm.chosen("Apache-2.0 OR GPL-2.0-or-later"), ("Apache-2.0",))
        self.assertEqual(nm.chosen("MIT AND MIT"), ("MIT",))
        self.assertEqual(nm.chosen("MPL-2.0 OR LGPL-2.1-only"), ("MPL-2.0",))

    def test_texts_follow_the_chosen_terms(self) -> None:
        self.assertEqual(
            nm.texts_for("Apache-2.0 WITH LLVM-exception"),
            ["texts/Apache-2.0.txt", "texts/LLVM-exception.txt"],
        )
        self.assertEqual(nm.texts_for("LicenseRef-FatFs"), [])


class CategoryTests(unittest.TestCase):
    CASES: ClassVar[dict[str, str]] = {
        "MIT": "permissive",
        "Unicode-3.0": "permissive",
        "MPL-2.0": "weak",
        "LicenseRef-NSIS": "weak",
        "LicenseRef-newlib": "permissive",
        "GPL-3.0-only": "copyleft",
        "GPL-2.0-or-later": "copyleft",
        "GPL-3.0-or-later WITH GCC-exception-3.1": "copyleft",
        "LGPL-2.1-or-later": "copyleft",
        "AGPL-3.0-only": "copyleft",
        "CC-BY-NC-4.0": "noncommercial",
        "CC-BY-NC-SA-4.0": "noncommercial",
        "LicenseRef-NonCommercial-Research": "noncommercial",
        "SSPL-1.0": "unknown",
        "LicenseRef-file:LICENSE": "unknown",
        "LicenseRef-FatFs WITH Something": "unknown",
    }

    def test_each_term_lands_in_its_category(self) -> None:
        for term, want in self.CASES.items():
            with self.subTest(term=term):
                self.assertEqual(nm.category(term), want)


class PolicyTests(unittest.TestCase):
    def errors(self, c: Component) -> list[str]:
        return nm.policy_errors([c])

    def test_permissive_with_a_copyright_line_ships(self) -> None:
        self.assertEqual(self.errors(comp("MIT")), [])
        self.assertEqual(self.errors(comp("MIT OR Apache-2.0")), [])

    def test_gpl_without_an_override_is_an_error(self) -> None:
        for lic in (
            "GPL-3.0-only",
            "GPL-2.0-or-later",
            "LGPL-2.1-only",
            "AGPL-3.0-only",
        ):
            with self.subTest(lic=lic):
                errs = self.errors(comp(lic))
                self.assertTrue(any("no recorded override" in e for e in errs), errs)

    def test_gpl_with_an_override_ships(self) -> None:
        self.assertEqual(self.errors(comp("GPL-3.0-only", override="because")), [])

    def test_agpl_still_fails_with_an_override_until_its_text_is_pinned(self) -> None:
        errs = self.errors(comp("AGPL-3.0-only", override="because"))
        self.assertEqual(errs, ["thing 1.0: no licence text for AGPL-3.0-only"])

    def test_a_dual_licence_complies_with_its_permissive_half(self) -> None:
        self.assertEqual(self.errors(comp("MIT OR GPL-3.0-only")), [])

    def test_noncommercial_never_ships_override_or_not(self) -> None:
        errs = self.errors(comp("CC-BY-NC-4.0", override="because"))
        self.assertTrue(any("forbids commercial use" in e for e in errs), errs)

    def test_weak_copyleft_needs_a_source_location(self) -> None:
        self.assertIn("needs a source location", " ".join(self.errors(comp("MPL-2.0"))))
        self.assertEqual(self.errors(comp("MPL-2.0", source="https://x")), [])

    def test_a_licence_ref_needs_its_own_text(self) -> None:
        errs = self.errors(comp("LicenseRef-FatFs"))
        self.assertTrue(any("no licence text of its own" in e for e in errs), errs)
        ok = comp("LicenseRef-FatFs", texts=("components/fatfs-ff.h-header.txt",))
        self.assertEqual(self.errors(ok), [])

    def test_an_exception_without_a_pinned_text_is_an_error(self) -> None:
        errs = self.errors(comp("MIT WITH Nobody-exception"))
        self.assertEqual(errs, ["thing 1.0: no licence text for Nobody-exception"])

    def test_unknown_licences_and_texts_and_missing_copyright_are_errors(self) -> None:
        self.assertIn(
            "not a licence the policy knows", self.errors(comp("SSPL-1.0"))[0]
        )
        self.assertIn(
            "not a licence the policy knows",
            self.errors(comp("LicenseRef-file:LICENSE"))[0],
        )
        self.assertIn("no copyright line", self.errors(comp("MIT", copyright=()))[0])
        bad = comp("MIT", texts=("components/nope.txt",))
        self.assertIn("unknown licence text", self.errors(bad)[0])
        self.assertIn("cannot read", self.errors(comp("MIT $"))[0])


class TextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        for sub in ("texts", "components"):
            shutil.copytree(nm.LICENSES / sub, self.tmp / sub)

    def test_the_committed_texts_are_the_pinned_ones(self) -> None:
        self.assertEqual(nm.text_errors(), [])
        self.assertEqual(nm.text_errors(self.tmp), [])

    def test_an_edited_text_fails(self) -> None:
        path = self.tmp / "texts" / "MIT.txt"
        path.write_text(
            path.read_text(encoding="utf-8") + "extra\n", encoding="utf-8", newline="\n"
        )
        self.assertEqual(
            nm.text_errors(self.tmp),
            [
                "licenses/texts/MIT.txt differs from the copy pinned in notices_model.TEXTS"
            ],
        )

    def test_a_missing_or_unpinned_text_fails(self) -> None:
        (self.tmp / "texts" / "ISC.txt").unlink()
        (self.tmp / "components" / "stray.txt").write_text(
            "x\n", encoding="utf-8", newline="\n"
        )
        self.assertEqual(
            nm.text_errors(self.tmp),
            [
                "licenses/texts/ISC.txt is missing",
                "licenses/components/stray.txt is not pinned in notices_model.TEXTS",
            ],
        )

    def test_newlib_is_put_back_together_byte_for_byte(self) -> None:
        text = nm.licence_text("components/newlib-COPYING.NEWLIB.txt")
        self.assertEqual(len(text.splitlines()), 1293)
        self.assertEqual(
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "422aa40293093fb54fc66e692a0d68fd0b24ed5602e5d1d33ad05ba3909057e9",
        )

    def test_a_text_without_a_final_newline_gets_one(self) -> None:
        (self.tmp / "texts" / "MIT.txt").write_text(
            "no newline", encoding="utf-8", newline="\n"
        )
        self.assertEqual(nm.licence_text("texts/MIT.txt", self.tmp), "no newline\n")


class CopyrightLineTests(unittest.TestCase):
    def test_statements_are_kept_once_in_order(self) -> None:
        text = (
            "MIT License\n\n  Copyright (c) 2020   Alice\n"
            "(c) 2019 Bob\n© 2021 Carol\nCopyright (c) 2020 Alice\n"
        )
        self.assertEqual(
            nd.copyright_lines([text]),
            ["Copyright (c) 2020 Alice", "(c) 2019 Bob", "© 2021 Carol"],
        )

    def test_licence_template_lines_are_not_statements(self) -> None:
        template = "\n".join(
            [
                "Copyright [yyyy] [name of copyright owner]",
                "Copyright <year> <copyright holders>",
                "copyright notice and this permission notice shall be included",
                "(c) You must retain, in the Source form of any Derivative Works",
                "Copyright:",
                "copyright protection under copyright law",
                "Copyright {yyyy} {name of copyright owner}",
            ]
        )
        self.assertEqual(nd.copyright_lines([template]), [])


class OwnLicenceTests(unittest.TestCase):
    NOT_MIT = (
        "LICENSE is not licenses/texts/MIT.txt with its copyright line "
        "filled in as 'Copyright (c) 2026 jtn0123'"
    )

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        for rel in ("LICENSE", *nr.OWN_MANIFESTS):
            (self.tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / rel, self.tmp / rel)

    def write(self, rel: str, text: str) -> None:
        (self.tmp / rel).write_text(text, encoding="utf-8", newline="\n")

    def test_the_tree_is_mit_wherever_it_says(self) -> None:
        self.assertEqual(nr.OWN_LICENCE, "MIT")
        self.assertEqual(nr.own_licence_errors(), [])
        self.assertEqual(nr.own_licence_errors(self.tmp), [])
        head = (ROOT / "LICENSE").read_text(encoding="utf-8").splitlines()[:3]
        self.assertEqual(head, ["MIT License", "", "Copyright (c) 2026 jtn0123"])

    def test_a_licence_that_stops_being_mit_fails(self) -> None:
        mit = nm.licence_text("texts/MIT.txt")
        for name, text in (
            ("another licence", nm.licence_text("texts/Apache-2.0.txt")),
            ("the template's copyright line", mit),
            (
                "a line added",
                (self.tmp / "LICENSE").read_text(encoding="utf-8") + "x\n",
            ),
        ):
            with self.subTest(name):
                self.write("LICENSE", text)
                self.assertEqual(nr.own_licence_errors(self.tmp), [self.NOT_MIT])
        (self.tmp / "LICENSE").unlink()
        self.assertEqual(nr.own_licence_errors(self.tmp), [self.NOT_MIT])

    def test_a_manifest_that_names_another_licence_or_none_fails(self) -> None:
        cargo = (self.tmp / "core" / "Cargo.toml").read_text(encoding="utf-8")
        self.write("core/Cargo.toml", cargo.replace('"MIT"', '"Apache-2.0"'))
        lock_path = self.tmp / "web" / "package-lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        del lock["packages"][""]["license"]
        self.write("web/package-lock.json", json.dumps(lock))
        self.write("desktop/src-tauri/tauri.conf.json", "{}")
        self.assertEqual(
            nr.own_licence_errors(self.tmp),
            [
                f"{where} is {found}, not 'MIT' like LICENSE"
                for where, found in (
                    ("core/Cargo.toml: package.license", "'Apache-2.0'"),
                    ("desktop/src-tauri/tauri.conf.json: bundle.license", "None"),
                    ('web/package-lock.json: packages."".license', "None"),
                )
            ],
        )

    def test_the_windows_installer_asks_nobody_to_agree_to_it(self) -> None:
        """`bundle.license` is metadata. `licenseFile` would put an "I agree"
        page in the NSIS installer, and a buyer has nothing to agree to."""
        for name in ("tauri.conf.json", "tauri.release.conf.json"):
            path = ROOT / "desktop" / "src-tauri" / name
            bundle = json.loads(path.read_text(encoding="utf-8"))["bundle"]
            with self.subTest(name):
                self.assertNotIn("licenseFile", bundle)

    def test_the_notices_say_what_the_licence_covers(self) -> None:
        mit = "own work (copyright jtn0123), under the MIT License (see LICENSE at the top"
        texts = {
            key: " ".join((ROOT / a.path).read_text(encoding="utf-8").split())
            for key, a in nr.ARTIFACTS.items()
        }
        for key, text in texts.items():
            with self.subTest(key):
                self.assertIn(mit, text)
        whole = "the image as a whole is"
        self.assertIn(
            f"{whole.capitalize()} not under the MIT License.", texts["firmware"]
        )
        self.assertIn(
            f"{whole} conveyed under the GNU General Public License version 3",
            texts["firmware"],
        )


if __name__ == "__main__":
    unittest.main()
