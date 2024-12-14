import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
import plotly.graph_objects as go
from pathlib import Path
from pdf2image import convert_from_path
import base64
from urllib.parse import urlencode, parse_qs
from exercise_db_builder import  get_exercise_pages, extract_exercises, enrich_with_toc, save_exercises, extract_solution
import os
import tempfile
import pyperclip
from dotenv import load_dotenv
load_dotenv()

# Initialize connection to database
def init_connection():
    return sqlite3.connect('exercises.db')

def get_exercises(conn, tags=None, references=None, attempt_status=None):
    """Get exercises with enhanced filtering options"""
    query = """
    SELECT e.*,
           COUNT(DISTINCT a.attempted_on) as attempt_count,
           MAX(a.is_solution_correct) as is_solved
    FROM exercises e
    LEFT JOIN attempts a
        ON e.reference = a.reference
        AND e.page = a.page
        AND e.number = a.number
    """

    conditions = []
    params = []

    if tags:
        tag_conditions = []
        for tag in tags:
            tag_conditions.append("e.tags LIKE ?")
            params.append(f"%{tag}%")
        if tag_conditions:
            conditions.append(f"({' OR '.join(tag_conditions)})")

    if references:
        ref_conditions = []
        for ref in references:
            ref_conditions.append("e.reference = ?")
            params.append(ref)
        if ref_conditions:
            conditions.append(f"({' OR '.join(ref_conditions)})")

    if attempt_status:
        if 'not_attempted' in attempt_status:
            conditions.append("COUNT(DISTINCT a.attempted_on) = 0")
        if 'attempted' in attempt_status:
            conditions.append("COUNT(DISTINCT a.attempted_on) > 0")
        if 'correct' in attempt_status:
            conditions.append("MAX(a.is_solution_correct) = 1")
        if 'incorrect' in attempt_status:
            conditions.append("MAX(a.is_solution_correct) =  0")

    query += " GROUP BY e.reference, e.page, e.number"

    if conditions:
        query += " HAVING " + " AND ".join(conditions)
    return pd.read_sql_query(query, conn, params=params)


def get_attempts(conn, reference, page, number):
    query = """
    SELECT *
    FROM attempts
    WHERE reference = ? AND page = ? AND number = ?
    ORDER BY attempted_on DESC
    """
    return pd.read_sql_query(query, conn, params=[reference, page, number])

def get_all_tags(conn):
    query = "SELECT DISTINCT tags FROM exercises"
    df = pd.read_sql_query(query, conn)
    all_tags = set()
    for tags_str in df['tags']:
        if tags_str:
            all_tags.update([tag.strip() for tag in tags_str.split(',')])
    return sorted(list(all_tags))

def get_all_references(conn):
    query = "SELECT DISTINCT reference FROM exercises"
    df = pd.read_sql_query(query, conn)
    return sorted(df['reference'].tolist())


def solutions_page(conn):
    if 'selected_exercise' not in st.session_state:
        st.error("No exercise selected")
        return
    
    exercise = st.session_state.selected_exercise
    
    # Get exercise details
    exercise_query = """
    SELECT * FROM exercises 
    WHERE reference = ? AND page = ? AND number = ?
    """
    exercise_df = pd.read_sql_query(
        exercise_query, 
        conn, 
        params=[exercise['reference'], exercise['page'], exercise['number']]
    )
    
    if exercise_df.empty:
        st.error("Exercise not found")
        return
    
    # Display exercise information
    col1, col2 = st.columns([3, 1])
    with  col1:
        st.markdown(f"### {exercise['reference']} - Exercise {exercise['number']} (Page {exercise['page']})")
    with col2:

        share_url = '#'
        if 'selected_exercise' in st.session_state:
            exercise = st.session_state.selected_exercise
            encoded_params = encode_exercise_params(
                exercise['reference'],
                exercise['page'],
                exercise['number']
            )
            share_url = get_share_url(encoded_params)
        if st.button("Copy Link", key="solution_copy_link"):
            pyperclip.copy(share_url)
            st.write("Link copied!")

    st.write(exercise_df.iloc[0]['text'])
    
    if exercise_df.iloc[0]['tags']:
        with st.expander("Show Tags", expanded=False):
            tags = [tag.strip() for tag in exercise_df.iloc[0]['tags'].split(',')]
            st.markdown(
                ' '.join([f'<span style="background-color: #e1e4e8; padding: 2px 6px; border-radius: 3px; margin-right: 5px;">{tag}</span>' 
                         for tag in tags]),
                unsafe_allow_html=True
            )
    
    # Add solution upload section
    st.markdown("### Submit New Solution")
    with st.expander("Upload Solution", expanded=True):
        # Initialize session state for uploaded files if not exists
        if 'uploaded_solution_files' not in st.session_state:
            st.session_state.uploaded_solution_files = []
        
        # File uploader
        # cache_key = st.query_params.get("solutions_uploader", None)

        uploaded_files = st.file_uploader(
            "Upload your solution (image)",
            type=['png', 'jpg', 'jpeg'],
            key='solutions_uploader',
            accept_multiple_files=True,
            help="Upload images of your solution. You can upload multiple images.",
        )
        # Add file to session state when uploaded
        # if uploaded_files is not None:
            # st.session_state.uploaded_solution_files += [f for f in uploaded_files if f not in st.session_state.uploaded_solution_files]
        st.session_state.uploaded_solution_files = uploaded_files
        
        # Display uploaded files
        if st.session_state.uploaded_solution_files:
            st.write(f"Uploaded files ({len(st.session_state.uploaded_solution_files)}):")
            
            # Create columns for the images
            cols = st.columns(min(3, len(st.session_state.uploaded_solution_files)))
            
            # Display images in columns
            for idx, file in enumerate(st.session_state.uploaded_solution_files):
                col = cols[idx % 3]
                with col:
                    st.image(file, caption=f"Image {idx + 1}", use_container_width=True)

            
            if len(st.session_state.uploaded_solution_files) > 0:
                if st.button("Process Solution", key="process_solution_btn"):
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        # Save all uploaded files
                        tmp_file_paths = []
                        for idx, file in enumerate(st.session_state.uploaded_solution_files):
                            file_ext = os.path.splitext(file.name)[1]
                            tmp_file_path = os.path.join(tmp_dir, f"solution_part_{idx}{file_ext}")
                            with open(tmp_file_path, "wb") as f:
                                f.write(file.getvalue())
                            tmp_file_paths.append(tmp_file_path)
                        
                        try:
                            # Create a progress indicator
                            with st.spinner('Processing your solution...'):
                                # Assume process_solution is modified to handle multiple images
                                solution_text, is_correct, feedback = extract_solution(tmp_file_paths, exercise_df.text.iloc[0])
                                
                                # Save attempt to database
                                current_date = datetime.now().strftime('%Y-%m-%d')
                                with sqlite3.connect('exercises.db') as con:
                                    cur = con.cursor()
                                    cur.execute("""
                                        INSERT INTO attempts 
                                        (reference, page, number, solution, is_solution_correct, solution_feedback, attempted_on)
                                        VALUES (?, ?, ?, ?, ?, ?, ?)
                                    """, (
                                        exercise['reference'],
                                        exercise['page'],
                                        exercise['number'],
                                        solution_text,
                                        is_correct,
                                        feedback,
                                        current_date
                                    ))
                                    con.commit()
                                
                                # Show result
                                st.success("Solution processed successfully!")
                                status_color = "🟢" if is_correct else "🔴"
                                st.markdown(f"{status_color} **Result:** {'Correct' if is_correct else 'Incorrect'}")
                                st.markdown("**Extracted Solution:**")
                                st.write(solution_text)
                                st.markdown("**Feedback:**")
                                st.write(feedback)
                                
                                # Clear uploaded files after successful processing
                                st.session_state.uploaded_solution_files = []
                                
                                # Add a button to refresh the page
                                if st.button("View All Attempts", key="refresh_attempts"):
                                    st.rerun()
                        
                        except Exception as e:
                            st.error(f"Error processing solution: {str(e)}")
    
    # Show previous attempts
    st.markdown("### Previous Attempts")
    attempts_df = get_attempts(conn, exercise['reference'], exercise['page'], exercise['number'])
    
    if attempts_df.empty:
        st.info("No previous attempts recorded for this exercise")
    else:
        for _, attempt in attempts_df.iterrows():
            with st.expander(
                f"Attempt from {attempt['attempted_on']} - {'✅ Correct' if attempt['is_solution_correct'] else '❌ Incorrect'}"
            ):
                st.write("Solution:")
                st.write(attempt['solution'])
                
                if attempt['solution_feedback']:
                    st.write("Feedback:")
                    st.write(attempt['solution_feedback'])
    
    if st.button("Back to Main Page", key="back_btn"):
        st.session_state.page = 'main'
        st.query_params.clear()

        st.rerun()




def process_document(file_path, progress_bar, status_text):
    """Process a document and extract exercises with progress tracking"""
    try:
        # Initialize
        reference = '.'.join(os.path.basename(file_path).split('.')[:-1])
        status_text.text("Converting PDF to images...")
        progress_bar.progress(0)
        
        # Convert PDF to images
        page_imgs = convert_from_path(file_path)
        progress_bar.progress(20)
        
        # Get exercise pages
        status_text.text("Identifying exercise pages...")
        pages = get_exercise_pages(file_path)

        if not pages:
            pages = list(range(1, len(page_imgs) + 1))
        progress_bar.progress(30)
        
        # Check existing pages
        status_text.text("Checking for existing exercises...")
        with sqlite3.connect('exercises.db') as con:
            query_pages = ', '.join(map(str, pages))
            existing_pages = pd.read_sql(
                f"SELECT reference, page FROM exercises WHERE page IN ({query_pages}) AND reference LIKE '{reference}'", 
                con
            )
            existing_pages = existing_pages.page.unique()
        
        pages = list(filter(lambda x: x not in existing_pages, pages))
        progress_bar.progress(40)
        
        if not pages:
            status_text.text("No new exercises to extract!")
            progress_bar.progress(100)

            return None
        
        # Extract exercises
        status_text.text("Extracting exercises...")
        progress_bar.progress(70)
        prog_incr = 30/len(pages)
        progress = 70

        num_exercises = 0

        for p in pages:
            status_text.text(f"Processing page {p}...")
            im = page_imgs[p - 1]
            exercises = extract_exercises(im, reference)

            for e in exercises:
                e['chapter'] = ''
                e['page'] = p
                e['reference'] = reference
                e['created_on'] = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
            ex_df = pd.DataFrame(exercises)

            if ex_df.empty:
                continue
            ex_df.tags = ex_df.tags.map(lambda x: ','.join(x))
            enrich_with_toc(file_path, ex_df)
            save_exercises(ex_df)
            num_exercises += ex_df.shape[0]
            progress += prog_incr
            progress_bar.progress(int(progress))
        
        # Save to database
        status_text.text("Saving to database...")
        progress_bar.progress(100)
        
        status_text.text("Processing complete!")

        return num_exercises
        
    except Exception as e:
        status_text.error(f"Error processing document: {str(e)}")

        return None

def main():
    st.set_page_config(page_title="Exercise Dashboard", layout="wide")
    
    query_params = st.query_params

    if 'ex' in query_params:
        exercise_params = decode_exercise_params(query_params['ex'])

        if exercise_params:
            st.session_state.page = 'solutions'
            st.session_state.selected_exercise = exercise_params
    # Initialize session state

    if 'page' not in st.session_state:
        st.session_state.page = 'main'
    
    conn = init_connection()
    
    if st.session_state.page == 'main':
        # Move filters to sidebar
        with st.sidebar:
            st.header("Filters")
            
            # Reference filter
            st.subheader("References")
            all_references = get_all_references(conn)
            selected_references = st.multiselect(
                "Select references",
                options=all_references,
                key="references_filter"
            )
            
            # Tags filter
            st.subheader("Tags")
            all_tags = get_all_tags(conn)
            # Add search box for tags
            tag_search = st.text_input("Search tags", key="tag_search").lower()
            filtered_tags = [tag for tag in all_tags if tag_search in tag.lower()]
            selected_tags = st.multiselect(
                "Select tags",
                options=filtered_tags,
                key="tags_filter"
            )
            
            # Attempt status filter
            st.subheader("Attempt Status")
            status_options = {
                'not_attempted': 'Not Attempted',
                'attempted': 'Attempted',
                'correct': 'Correct',
                'incorrect': 'Incorrect'
            }
            selected_status = st.multiselect(
                "Select status",
                options=list(status_options.keys()),
                format_func=lambda x: status_options[x],
                key="status_filter"
            )
            
            # Add import section to sidebar
            st.subheader("Import Documents")
            with st.expander("Upload Document"):
                uploaded_file = st.file_uploader(
                    "Choose a PDF file",
                    type=['pdf'],
                    key="pdf_uploader"
                )
                
                if uploaded_file is not None:
                    if st.button("Process Document", key="process_button"):
                        with tempfile.TemporaryDirectory() as tmp_dir:
                            tmp_file_path = os.path.join(tmp_dir, uploaded_file.name)
                            with open(tmp_file_path, "wb") as f:
                                f.write(uploaded_file.getvalue())
                            
                            try:
                                progress_bar = st.progress(0, key="progress")
                                status_text = st.empty()
                                
                                result = process_document(tmp_file_path, progress_bar, status_text)
                                
                                if result is not None:
                                    st.success(f"Successfully processed {len(result)} exercises!")
                                    st.rerun()
                            
                            except Exception as e:
                                st.error(f"Error processing document: {str(e)}")
        
        # Main content area
        st.title("Exercise Dashboard")
        
        # Get and display exercises with filters
        exercises_df = get_exercises(conn, selected_tags, selected_references, selected_status)
        
        # Display exercise count
        total_exercises = len(exercises_df)
        filter_active = bool(selected_tags or selected_references or selected_status)
        
        if filter_active:
            st.markdown(f"**Showing {total_exercises} filtered exercises**")
        else:
            st.markdown(f"**Total exercises: {total_exercises}**")
        
        # Display exercises

        if exercises_df.empty:
            st.info("No exercises found matching the selected filters")
        else:
            for _, exercise in exercises_df.iterrows():
                with st.container():
                    st.markdown("---")
                    display_exercise_panel(exercise)
    
    elif st.session_state.page == 'solutions':
        solutions_page(conn)
        # Add share button in solutions page

            
    conn.close()

def get_share_url(encoded_params):
    """Generate shareable URL based on configuration"""
    base_url = os.getenv('BASE_URL')

    if base_url:
        return f"{base_url}?ex={encoded_params}"

    return None

def encode_exercise_params(reference, page, number):
    """Encode exercise parameters for URL"""
    params = {
        'ref': reference,
        'page': str(page),
        'num': str(number)
    }

    return base64.urlsafe_b64encode(urlencode(params).encode()).decode()

def decode_exercise_params(encoded_params):
    """Decode exercise parameters from URL"""
    try:
        decoded = base64.urlsafe_b64decode(encoded_params.encode()).decode()
        params = parse_qs(decoded)

        return {
            'reference': params['ref'][0],
            'page': int(params['page'][0]),
            'number': int(params['num'][0])
        }
    except:
        print('failed')
        print(encoded_params)

        return None

def display_exercise_panel(exercise):
    with st.container():
        col1, col2 = st.columns([4, 1])
        
        with col1:
            st.markdown(f"**{exercise['reference']} - Exercise {exercise['number']} (Page {exercise['page']})**")
            st.write(exercise['text'])
            
            # Add tags in a collapsible section

            if exercise['tags']:
                with st.expander("Show Tags", expanded=False):
                    tags = [tag.strip() for tag in exercise['tags'].split(',')]
                    st.markdown(
                        ' '.join([f'<span style="background-color: #e1e4e8; padding: 2px 6px; border-radius: 3px; margin-right: 5px;">{tag}</span>' 
                                 for tag in tags]),
                        unsafe_allow_html=True
                    )
            
        
        with col2:
            attempt_count = exercise['attempt_count']
            is_solved = exercise['is_solved']
            
            if attempt_count == 0:
                st.markdown("⚪ Not attempted")
            elif is_solved:
                st.markdown("🟢 Solved")
            else:
                st.markdown("🔴 Unsolved")
            
            st.write(f"Attempts: {attempt_count}")
            
            if st.button("View Solutions", key=f"btn_{exercise['reference']}_{exercise['page']}_{exercise['number']}"):
                st.session_state.page = 'solutions'
                st.session_state.selected_exercise = {
                    'reference': exercise['reference'],
                    'page': exercise['page'],
                    'number': exercise['number']
                }
                st.rerun()

            encoded_params = encode_exercise_params(
                exercise['reference'],
                exercise['page'],
                exercise['number']
            )
            share_url = f"192.168.0.10:8501?ex={encoded_params}"

            if st.button("Copy Link", key=f"copy_{exercise['reference']}_{exercise['page']}_{exercise['number']}"):
                pyperclip.copy(share_url)
                st.write("Link copied!")  # This is just visual feedback

if __name__ == "__main__":
    main()
