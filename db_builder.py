from pdf2image import convert_from_path
import os
import subprocess as sp
from anthropic import Anthropic
import numpy as np
from dotenv import load_dotenv
import base64
from io import BytesIO
import json
from collections import defaultdict
import sqlite3
from sqlite3 import OperationalError
import logging
from datetime import datetime
from PIL import Image
from typing import List
import markdown
import toc
from bs4 import BeautifulSoup
import pandas as pd

sqlite3.register_adapter(np.int64, lambda val: int(val))
sqlite3.register_adapter(np.int32, lambda val: int(val))
sqlite3.register_adapter(np.bool, lambda val: bool(val))

logger = logging.getLogger()

EXERCISE_MARKER = '(esx10)'
DB = 'exercises.db'
load_dotenv()

def setup():
    with sqlite3.connect(DB) as con:
        cur = con.cursor()
        try:
            cur.execute("""
            CREATE TABLE exercises(
                reference VARCHAR,
                chapter VARCHAR,
                page INTEGER, 
                number INTEGER, 
                text VARCHAR, 
                has_figure BOOLEAN, 
                tags VARCHAR, 
                created_on DATE
                )
            """)
            cur.execute("""
            CREATE TABLE attempts(
                reference VARCHAR,
                page INTEGER, 
                number INTEGER, 
                solution VARCHAR DEFAULT NULL, 
                is_solution_correct BOOLEAN,
                solution_feedback VARCHAR DEFAULT NULL, 
                attempted_on DATE
            )
            """)
            con.commit()
        except OperationalError:
            print('table already exists.')
            pass


def reset():
    with sqlite3.connect(DB) as con:
        cur = con.cursor()
        try:
            cur.execute('DROP TABLE exercises')
            cur.execute('DROP TABLE attempts')
            con.commit()
        except OperationalError:
            pass


def get_exercise_pages(file_path: str) -> List[int]:

    out = sp.Popen(['pdfgrep', '-n', EXERCISE_MARKER, file_path], stdout=sp.PIPE, stderr=sp.PIPE )
    pages = list(map(lambda x: int(x.decode('utf-8').split(':')[0]), out.stdout.readlines()))

    return pages

def extract_exercises(im: Image, reference: str) -> dict:
    client = Anthropic()

    PROMPT = """
    this is a page extracted from a math book containing some exercises. 
    I would like you to extract all the exercises shown in this page.
    The exercises must be grouped reasonably and each exercise's layout must be preserved.
    The extracted exercises should be returned as a list of json objects.
    Each json representing an exercise should have the following format:
    {
        'number': the exercise number
        'text': markdown formatted text of the exercise. All mathematical notation should be represented in latex delimited by '$' ,
        'has_figure': true of false depending on whether the exercise has an associated figure in the page,
        'tags': list of sub-topics covered by the exercise within the context of the topic , the tags should include all the specific concepts strenghtened by solving the exercise
    }
    only output a list of json objects representing the exercises.
    """
    buffered = BytesIO()
    im.save(buffered, format='PNG')
    data = base64.b64encode(buffered.getvalue()).decode('utf-8')
    message = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=8192,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": data,
                        },
                    },
                    {
                        "type": "text",
                        "text": PROMPT
                    }
                ],
            }
        ],
    )
    try:
        page_exercises =  json.loads(message.content[0].text)
    except Exception as e:
        print(f'failed to extract exercises from page : {e}')
        return []
    return page_exercises

def extract_solution(img_paths: str, ex_instruction: str) -> dict:
    solution_images = [Image.open(path) for path in img_paths]

    client = Anthropic()

    solution_msg = []
    for im in solution_images:
        buffered = BytesIO()
        im.save(buffered, format='PNG')
        data = base64.b64encode(buffered.getvalue()).decode('utf-8')

        solution_msg.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": data,
                            },
                        })

    PROMPT = f"""
    The image provided gives a solution attempt to the following exercise
    <exercise>
    {ex_instruction}
    </exercise>

    analyse the solution and generate a json with the following 

    {{
        'solution_text': a markdown of the transcription of the solution with all the math notation in LATEX delimited by $
        'is_solution_correct': true/false based on whether the attempt is correct
        'feedback': feedback on the solution. this should cover in the details the approach of the attempt, if it's wrong only given the reason why, don't provide the complete solution. also add remarks concerning the actual attempt structure.
    }}
    only output the json
    """
    message = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=8192,
        messages=[
            {
                "role": "user",
                "content": [
                    *solution_msg,
                    {
                        "type": "text",
                        "text": PROMPT
                    }
                ],
            }
        ],
    )
    solution =  json.loads(message.content[0].text)
    return solution['solution_text'], solution['is_solution_correct'], solution['feedback']

def save_exercises(exercises: pd.DataFrame) -> None:

    with sqlite3.connect(DB) as con:

        cur = con.cursor()
        cols = ','.join(exercises.columns)
        bindings = ', '.join(['?'] * len(exercises.columns))
        
        cur.executemany(f'INSERT INTO exercises ({cols}) VALUES({bindings}) ', exercises.to_records(index=False))
        con.commit()



def enrich_with_toc(file_path: str, exercises: pd.DataFrame):

    file_toc = toc.pdf_to_toc(file_path)

    toc_html = markdown.markdown(file_toc)
    toc_soup = BeautifulSoup(toc_html, 'html.parser')
    headings = []

    for lvl in [0, 1]:
        headings += toc.get_headings(toc_soup, lvl)
    headings =  pd.DataFrame(headings)
    headings = headings.sort_values(by='page')
    headings['chapter'] = pd.NA
    headings.loc[headings.level == 0, 'chapter']= headings.loc[headings.level == 0, 'title'].values
    headings.chapter = headings.chapter.ffill()
    for i, e in exercises.iterrows():
         section  = headings.loc[headings.page[headings.page < e.page].idxmax() ]
         # headings[headings.page < section.page].drop_duplicates(subset='level', keep='last')
         exercises.loc[i, 'chapter'] = section.chapter  
         chapter_content = ','.join(headings[headings.chapter == section.chapter].title.values)
         exercises.loc[i, 'tags']  += ',' + chapter_content

if __name__ == "__main__":

    file_path = '/home/cpt-n3m0/Enoch/Library/Ross - 2014 - A first course in probability.pdf'
    reference = '.'.join(os.path.basename(file_path).split('.')[:-1])
    page_imgs = convert_from_path(file_path)
    pages = get_exercise_pages(file_path)
    if pages == []:
        pages = list(range(1, len(page_imgs) + 1)) 
    with sqlite3.connect(DB) as con:
        query_pages = ', '.join(map(str, pages))
        existing_pages = pd.read_sql(f"SELECT reference, page FROM exercises WHERE page IN ({query_pages}) AND reference LIKE '{reference}'", con)
        existing_pages = existing_pages.page.unique()
    pages = list(filter(lambda x: x not in existing_pages, pages))

    # if there are no marked exercise pages use all the pages from the document
    exercises = extract_exercises(page_imgs, reference, pages[:3])
    ex_df = pd.DataFrame(exercises)
    ex_df.tags = ex_df.tags.map(lambda x: ','.join(x))
    enrich_with_toc(file_path, ex_df)
    save_exercises(ex_df)


