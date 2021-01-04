import json
import re
import requests
from bs4 import BeautifulSoup
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from enum import Enum

YEAR = 2020
SCHOOL_REGEX = re.compile(r"Duke")
MAX_REQUESTS = 20  # don't make this number too high
JSON_FILEPATH = "./out/out.json"

RaceStyle = Enum("RaceStyle", "votebox,table")

URL_PREFIX = "https://ballotpedia.org/"
URL_REGEX = re.compile(r"^(?:https?:\/\/)?(?:ballotpedia.org)?\/")
STATE_NAMES = [
    "Alabama",
    "Alaska",
    "Arizona",
    "Arkansas",
    "California",
    "Colorado",
    "Connecticut",
    "Delaware",
    "Florida",
    "Georgia",
    "Hawaii",
    "Idaho",
    "Illinois",
    "Indiana",
    "Iowa",
    "Kansas",
    "Kentucky",
    "Louisiana",
    "Maine",
    "Maryland",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Mississippi",
    "Missouri",
    "Montana",
    "Nebraska",
    "Nevada",
    "New_Hampshire",
    "New_Jersey",
    "New_Mexico",
    "New_York",
    "North_Carolina",
    "North_Dakota",
    "Ohio",
    "Oklahoma",
    "Oregon",
    "Pennsylvania",
    "Rhode_Island",
    "South_Carolina",
    "South_Dakota",
    "Tennessee",
    "Texas",
    "Utah",
    "Vermont",
    "Virginia",
    "Washington",
    "West_Virginia",
    "Wisconsin",
    "Wyoming",
    "American_Samoa",
    "Guam",
    "Northern_Mariana_Islands",
    "Puerto_Rico",
    "U.S._Virgin_Islands",
]
RACE_NAMES = {
    RaceStyle.votebox: {
        "President of the United States",
        "U.S. Senate",
        "U.S. House",
        "Congress special election",
        "Other state executive",
        "Special state legislative",
    },
    RaceStyle.table: {"State Senate", "State House"},
}

# Necessary regexes, precompiled
VOTEBOX_REGEX = re.compile(r"p?votebox")
OFFICE_REGEX = re.compile(r"Office")
PARTY_REGEX = re.compile(r"Affiliation")
EDU_REGEX = re.compile(r"Education")


def print_progress_bar(
    curr: int,
    total: int,
    prefix: str = "",
    suffix: str = "",
    length: int = 50,
    fill: str = "█",
    empty: str = "-",
):
    """
    Prints a progress bar.

    The printout uses \r to overwrite the previous line, and end="" to ensure the
    current line can be overwritten.

    Args:
        curr: Current progress
        total: Total progress
        prefix: String before progress bar
        suffix: String after progress bar
        length: Character length of progress bar
        fill: Fill character
        empty: Empty character
    """
    percent = ("{0:.1f}").format(100 * (curr / total))
    filled_len = int(length * curr // total)
    bar = f"{fill * filled_len}{empty * (length - filled_len)}"
    print(f"\r{prefix} |{bar}| {percent}% {suffix} ({curr}/{total})", end="")
    if curr >= total:
        print()  # progress bar complete


def parse_state(races: dict, url: str) -> None:
    """
    Parses a state for offices on the ballot.

    Args:
        races: Races as {RaceStyle: {urls}}
        url: Relative URL of Ballotpedia state elections page
    """

    page = requests.get(f"{URL_PREFIX}{url}")
    soup = BeautifulSoup(page.content, "html.parser")
    offices = soup.find("table", id="offices")
    if offices:
        for a in offices.find_all("a"):
            url = re.sub(URL_REGEX, "", a["href"])
            name = a.get_text().strip()
            if name in RACE_NAMES[RaceStyle.votebox]:
                races[RaceStyle.votebox].add(url)
            elif name in RACE_NAMES[RaceStyle.table]:
                races[RaceStyle.table].add(url)

    global progress
    progress += 1
    print_progress_bar(progress, max_progress, "Parsing states:    ", "complete")


def parse_race(cands: dict, url: str, style: RaceStyle) -> None:
    """
    Parses a race for candidates running.

    Args:
        cands: Candidates as {url: {"name": name, "races": {races}, etc}}
        url: Relative URL of Ballotpedia race page
        style: Style of Ballotpedia race page to be parsed
    """

    page = requests.get(f"{URL_PREFIX}{url}")
    soup = BeautifulSoup(page.content, "html.parser")

    if style == RaceStyle.votebox:
        voteboxes = soup.find_all("div", class_=VOTEBOX_REGEX)

        if voteboxes:
            for div in voteboxes:
                race_header = div.find("h5", class_="votebox-header-election-type")
                if race_header:
                    race = race_header.get_text().strip()
                    for td in div.find_all("td", class_="votebox-results-cell--text"):
                        for a in td.find_all("a"):
                            url = re.sub(URL_REGEX, "", a["href"])
                            name = a.get_text().strip()
                            cands[url]["name"] = name
                            cands[url]["races"].add(race)
    elif style == RaceStyle.table:
        tables = soup.find_all("table", class_="candidateListTablePartisan")

        if tables:
            for table in tables:
                race = table.h4.get_text().strip()
                header_found = False
                for tr in table.find_all("tr"):
                    if header_found and tr.td:
                        district = tr.td.get_text().strip()  # get 1st td
                        for span in tr.find_all("span", class_="candidate"):
                            a = span.a  # get 1st a
                            if a:
                                url = re.sub(URL_REGEX, "", a["href"])
                                name = a.get_text().strip()
                                cands[url]["name"] = name
                                cands[url]["races"].add(f"{race} {district}")
                    elif tr.find("td", string=OFFICE_REGEX):
                        header_found = True

    global progress
    progress += 1
    print_progress_bar(progress, max_progress, "Parsing races:     ", "complete")


def parse_cand(cands: dict, url: str) -> None:
    """
    Parses a candidate page for information.

    Candidate pages are not well standardized. This function tries 4 common formats
    for education, with the following possible outcomes:
        1. Adds an "edu" entry to cand[url] as {degree: school} if matching school is
           found.
        2. Same as #1, but degree is Unknown.
        3. Assigns "Marked for manual review" if no education is found.
    It also tries to find party affiliation.

    Args:
        cands: Candidates as {url: {"name": name, etc}}
        url: Relative URL of Ballotpedia candidate page
    """

    page = requests.get(f"{URL_PREFIX}{url}")
    soup = BeautifulSoup(page.content, "html.parser")

    cands[url]["edu"] = {}

    # Flags for party, education found
    party_found = False
    edu_found = False

    # Try infobox div if it exists
    widgets = soup.find_all("div", class_="widget-row")
    for div in widgets:
        if not party_found:
            try:
                party_ind = div["class"].index("Party")
                if party_ind > 0:
                    ind = div["class"].index("value-only")
                    cands[url]["party"] = " ".join(div["class"][ind + 1 : party_ind])
                    party_found = True
            except ValueError:
                pass

        if edu_found:
            if "value-only" in div["class"]:  # edu section is over
                break
            else:
                school = div.find("div", class_="widget-value").get_text().strip()
                if re.search(SCHOOL_REGEX, school):
                    degree = div.find("div", class_="widget-key").get_text().strip()
                    cands[url]["edu"][degree] = school
        elif div.find("p", string=EDU_REGEX):
            edu_found = True

    # Next, try infobox table if it exists
    if not edu_found:
        table = soup.find("table", class_="infobox")
        if table:
            for tr in table.find_all("tr"):
                if party_found and edu_found:
                    break
                tr_children = list(tr.children)
                if len(tr_children) >= 2:  # verify tr has at least 2 cols
                    if not party_found and tr.find("td", string=PARTY_REGEX):
                        cands[url]["party"] = tr_children[1].get_text().strip()
                        party_found = True
                    elif tr.find("td", string=EDU_REGEX):
                        td_text = tr_children[1].get_text().strip()
                        if re.search(SCHOOL_REGEX, td_text):
                            cands[url]["edu"]["Unknown"] = td_text
                        edu_found = True

    # Next, try bio section if it exists
    if not edu_found:
        content = soup.find("div", id="mw-content-text")
        if content:
            for elem in content.children:
                if edu_found:
                    if elem.name != "p":  # bio section is over
                        break
                    search = re.search(SCHOOL_REGEX, elem.get_text())
                    if search:
                        cands[url]["edu"]["Unknown"] = search.group()
                        break
                elif elem.name == "h2" and elem.find("span", id="Biography"):
                    edu_found = True

    # Next, try profile bio if it exists
    if not edu_found:
        profile_bio = soup.find("div", class_="cc_bio")
        if profile_bio:
            edu_found = True
            search = re.search(SCHOOL_REGEX, profile_bio.get_text())
            if search:
                cands[url]["edu"]["Unknown"] = search.group()

    # If nothing is found, mark for manual review
    if not edu_found:
        cands[url]["edu"]["Unknown"] = "Marked for manual review"

    global progress
    progress += 1
    print_progress_bar(progress, max_progress, "Parsing candidates:", "complete")


def main():
    """
    Main.
    """

    global progress
    global max_progress

    # Generate URLs of Ballotpedia state elections pages
    states = [f"{state}_elections,_{YEAR}" for state in STATE_NAMES]

    # Generate URLs of Ballotpedia race pages
    races = defaultdict(set)
    progress = 0
    max_progress = len(states)
    print_progress_bar(progress, max_progress, "Parsing states:    ", "complete")
    for state in states:
        parse_state(races, state)

    # Generate URLs of Ballotpedia candidate pages
    cands = defaultdict(lambda: {"races": set()})
    progress = 0
    max_progress = sum(len(l) for l in races.values())
    print_progress_bar(progress, max_progress, "Parsing races:     ", "complete")
    for style, urls in races.items():
        with ThreadPoolExecutor(max_workers=MAX_REQUESTS) as pool:
            list(pool.map(lambda url: parse_race(cands, url, style), urls))

    # Fetch candidate information
    progress = 0
    max_progress = len(cands)
    print_progress_bar(progress, max_progress, "Parsing candidates:", "complete")
    with ThreadPoolExecutor(max_workers=MAX_REQUESTS) as pool:
        list(pool.map(lambda url: parse_cand(cands, url), cands.keys()))

    # Print matching candidates
    print("Candidates with matching education:")
    manual_review = 0
    for url, info in list(cands.items()):
        if not info["edu"]:
            del cands[url]
        elif "Marked for manual review" in info["edu"].values():
            manual_review += 1
        else:
            edu = [f"{school} ({degree})" for degree, school in info["edu"].items()]
            print(f"{info['name']}: {', '.join(edu)}")
    print(f"...and {manual_review} marked for manual review")

    # Save full results
    with open(JSON_FILEPATH, "w") as f:
        json.dump(cands, f, indent=4, ensure_ascii=False)


if __name__ == "__main__":
    main()
