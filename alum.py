import re
import requests
from bs4 import BeautifulSoup

URL = "https://ballotpedia.org/Roy_Cooper"
page = requests.get(URL)

soup = BeautifulSoup(page.content, "html.parser")
widgets = soup.find_all("div", {"class": "widget-row"})

edu = {}
edu_found = False
for elem in widgets:
    if edu_found:
        if "value-only" in elem["class"]:
            break
        else:
            degree = elem.find("div", {"class": "widget-key"}).get_text().strip()
            school = elem.find("div", {"class": "widget-value"}).get_text().strip()
            edu[degree] = school
    elif elem.find("p", text=re.compile("\s*Education\s*")):
        edu_found = True

print(edu)
