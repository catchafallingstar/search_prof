"""Read-only checks against official rosters; no database or model calls.

Run: python -m scripts.check_roster_parser_live
Optional --cache-folder replays previously downloaded KEY.html files.
"""
import argparse
import json
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from ingestion.faculty_roster import classify_faculty_page, _canonical_profile_url

PAGES = [
    ('gsu_law', 'https://law.gsu.edu/directory/', 1, 2),
    ('gsu_lewis', 'https://lewis.gsu.edu/profile-directory/', 1, 2),
    ('gsu_cas', 'https://cas.gsu.edu/profile-directory/', 2, 1),
    ('gsu_education', 'https://education.gsu.edu/directory/', 1, 2),
    ('uwf', 'https://uwf.edu/offices/emerald-coast/about-emerald-coast/administration-faculty-staff/', None, None),
    ('csusb_comm', 'https://www.csusb.edu/communication-media/faculty', None, None),
    ('csusb_art', 'https://www.csusb.edu/art-grad/faculty', None, None),
    ('csusb_chem', 'https://www.csusb.edu/chemistry-biochemistry/faculty-staff', None, None),
]
EXPECTED = {
    'Dawn Aycock': 'Distinguished University Professor, PhD Program Director',
    'Katie Acosta': 'Associate Professor, Director of Graduate Studies',
    'Amrita Gautam': 'Assistant Professor',
    'Jeff McGuirk': 'Senior Instructor',
    'Bassam Shaer': 'Professor',
    'Ece Algan': 'Professor',
    'Raisa Alvarado': 'Associate Professor',
    'Francis Almendarez': 'Assistant Professor',
    'Andreas Beyersdorf': 'Professor',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-folder', type=Path)
    args = parser.parse_args()
    observed = {}
    summary = []
    for key, url, title_column, department_column in PAGES:
        if args.cache_folder:
            html = (args.cache_folder / f'{key}.html').read_text()
        else:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            html = response.text
        result = classify_faculty_page(html, url)
        assert result.status == 'APPROVED_ROSTER', (key, result.reason)
        by_url = {_canonical_profile_url(m.profile_url): m for m in result.members}
        checked = 0
        if title_column is not None:
            # Independently compare all parsed rows with their visible source
            # columns, whose ordering was checked on the official pages.
            soup = BeautifulSoup(html, 'html.parser')
            for row in soup.select('[id="profile-inside-row"], [id="profile_row"]'):
                cells = row.find_all('div', class_='vc_column_container', recursive=False)
                if len(cells) != 5:
                    continue
                anchor = cells[0].find('a', href=True)
                if anchor is None:
                    continue
                profile_url = requests.compat.urljoin(url, anchor['href'])
                member = by_url.get(_canonical_profile_url(profile_url))
                if member is None:
                    continue
                assert member.title == cells[title_column].get_text(' ', strip=True), (key, member.name, 'title mismatch')
                assert member.section_heading == cells[department_column].get_text(' ', strip=True), (key, member.name, 'department mismatch')
                checked += 1
            assert checked >= len(result.members) - 1, (key, checked, len(result.members))
        observed.update({m.name: m.title for m in result.members})
        summary.append({'page': key, 'members': len(result.members),
                        'blank_titles': sum(not m.title for m in result.members),
                        'source_rows_compared': checked})
    for name, title in EXPECTED.items():
        assert observed.get(name) == title, (name, observed.get(name), title)
    assert not any('headshot' in name.lower() for name in observed)
    print(json.dumps(summary, indent=2))
    print('All official-page parser checks passed.')


if __name__ == '__main__':
    main()
