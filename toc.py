import os
import re
import subprocess
import sys
import markdown

import yaml

def tabs_to_hash(match_str):
    return re.sub(r'\t', '#', match_str.group(0)) + ' '


def add_links(match_str, file_path):
    file = os.path.basename(file_path)

    return f'[[{file}#page={match_str.group(1)}|{match_str.group(1)}]]'


def remove_quotes(match_str):
    return "# "+match_str.group(1)[1:-1] + '\t'


def pdf_to_toc(file_path):

    sp = subprocess.Popen(['mutool', 'show',  file_path,
                           'outline'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    raw_toc = sp.stdout.read().decode('utf-8')
 
    md_toc = re.sub(r'(?<=\+|\|)\t+', tabs_to_hash,  raw_toc)
    md_toc = re.sub(r'^(\+|\|)', '', md_toc, flags=re.MULTILINE)
    md_toc = re.sub(r'#page=(\d+)', lambda x: add_links(x, file_path), md_toc)
    md_toc = re.sub(r'&view=Fit', '', md_toc)
    md_toc = re.sub(r'# (".+?")\t', remove_quotes, md_toc)

    return md_toc




def get_heading_parents(toc_soup, heading):
    header_lvl = int(heading['element'].name[1])

    parent_headers = []

    lvl = header_lvl - 1

    while lvl > 0:

        for h in heading['element'].previous_siblings:

            if h.name == f'h{lvl}':
                parent_headers.append(
                    (int(re.match(r'.*#page=(\d*).*', h.text).group(1)), h))

                break
        lvl -= 1

    return parent_headers


def compute_heading_intervals(headings):
    for i in range(len(headings)):
        if i == len(headings) - 1:
            headings[i]['interval'] = (headings[i]['page'], float('inf'))
        else:
            headings[i]['interval'] = (
                headings[i]['page'], headings[i + 1]['page'])

def get_headings(soup, level=0):

    headings = []

    for h in soup.find_all(f'h{level + 1}')[1:]:
        text = h.text
        title = re.sub(r' \[\[.*\]\]', '', text)
        heading_page_num = int(re.match(r'.*#page=(\d*).*', text).group(1))
        headings.append({'page': heading_page_num, 'title': title, 'level': level  })


    return headings

