import logging
import math
import os
import queue
import re
import threading
import tkinter as tk
import time

from html import unescape
from pathlib import Path
from tkinter import ttk

# Third-party modules
import pyrfc6266
import requests

from bs4 import BeautifulSoup

# TODO: remove
import ipdb

__version__ = "0.0.0a0"

logger = logging.getLogger("bookdl")
DEFAULT_LOGGING_LEVEL = 'Debug'
MIRROR_SOURCES = ["GET", "Cloudflare", "IPFS.io", "Crust", "Pinata"]

RESPONSE = None


class TKTextHandler(logging.Handler):
    def __init__(self, tktext):
        super().__init__()
        self.tktext = tktext

    def emit(self, record):
        msg = self.format(record)
        self.tktext.insert("end", msg+'\n')


# Ref.: https://github.com/carterprince/libby/blob/main/libby
def get_first_author(authors_str):
    authors_str = authors_str.replace(', ', '; ').replace(';', '; ')
    authors_str = re.sub(r'\s+', ' ', authors_str)
    authors = authors_str.split('; ')
    if len(authors[0].split(" ")) == 1 and len(authors) > 1:
        authors[0] += ", " + authors[1]

    return authors[0]


# Return "folder_path/basename" if no file exists at this path. Otherwise,
# sequentially insert " ($n)" before the extension of `basename` and return the
# first path for which no file is present.
# ref.: https://bit.ly/3n1JNuk
def unique_filename(folder_path, basename):
    stem = Path(basename).stem
    ext = Path(basename).suffix
    new_path = Path(Path(folder_path).joinpath(basename))
    counter = 0
    while new_path.is_file():
        counter += 1
        logger.debug(f"File '{new_path.name}' already exists in destination "
                     f"'{folder_path}', trying with counter {counter}!")
        new_stem = f'{stem} {counter}'
        new_path = Path(Path(folder_path).joinpath(new_stem + ext))
    return new_path.as_posix()


class EbookDownloader:
    def __init__(self, root, width=1280, height=800):
        self.root = root
        self.width = width
        self.height = height
        self.root.geometry(f"{self.width}x{self.height}+0+0")
        self.root.title("Libgen Downloader")
        self.book_ids_per_urls = {}
        self.books = {}
        self.filenames = {}
        self.url = None
        self.search_entry = None
        # TODO: table instead of tree
        self.search_tree = None
        self.page_var = None
        self.selected_items_from_search_tree = set()
        self.selected_items_from_download_tree = set()
        self.download_tree = None
        self.logging_text = None
        self.context_menu = None
        self.log_levels = [
            (tk.IntVar(), "Debug"),
            (tk.IntVar(), "Info"),
            (tk.IntVar(), "Warning"),
            (tk.IntVar(), "Error"),
        ]
        self.toggle_var = tk.IntVar()
        self.toggle_label = tk.StringVar()
        self.gui_update_queue = queue.Queue()
        self.filenames_by_threads = {}
        self.shared_nb_mirror1 = 0
        self.shared_nb_mirror2 = 0
        self.shared_download_queue = []
        self.shared_pause_thread = set()
        self.shared_resume_thread = set()
        self.shared_stop_thread = set()
        self.shared_nb_threads = 0
        self.first_search = False
        self.max_retries = 1
        self.delay_between_retries = 0.5
        self.chunk_size = 8192

        self.query = None
        # domains = [libgen.rocks, libgen.lc, libgen.li, libgen.gs, libgen.vg, libgen.pm]
        self.domain = "https://libgen.pm"
        # e.g. extensions = ['epub', 'pdf']
        # all extensions: extensions = ['all']
        self.extensions = ['all']
        # e.g. languages = ['english', 'french', 'spanish']
        # all languages: languages = ['all']
        self.languages = ['all']
        # e.g. mirrors = [1, 2, 3]
        # 1: libgen, 2: libgen.is, 3: annas-archive.org, 4: sci-hub.ru, 5: bookfi.net
        # TODO: not used
        self.mirrors = [1, 2]
        # results_per_page = 25 OR 50 OR 100
        self.results_per_page = 25
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "QtWebEngine/5.15.5 Chrome/87.0.4280.144 Safari/537.36"
        }

        # Separate locks for different resources
        self.lock_mirror1 = threading.Lock()
        self.lock_mirror2 = threading.Lock()
        self.lock_download_queue = threading.Lock()
        self.lock_pause_thread = threading.Lock()
        self.lock_resume_thread = threading.Lock()
        self.lock_stop_thread = threading.Lock()
        self.lock_nb_threads = threading.Lock()

        # Start a separate thread for GUI updates
        threading.Thread(target=self.gui_update_thread, daemon=True).start()

        # Create GUI elements
        self.logger_is_setup = False
        self.create_widgets()

        self.setup_logger()
        self.logger_is_setup = True
        logger.info(f"Logging level set to '{self.get_logging_level()}'")

    def setup_logger(self):
        logger.setLevel(DEFAULT_LOGGING_LEVEL.upper())

        # handler = TKTextHandler(self.logging_text)
        handler = logging.StreamHandler()
        # '%(asctime)s - %(levelname)s - %(message)s'
        formatter = logging.Formatter('%(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        handler.setLevel(DEFAULT_LOGGING_LEVEL.upper())

        # Add the handler to the logger
        logger.addHandler(handler)

    def gui_update_thread(self):
        while True:
            try:
                # Get updates from the queue
                update = self.gui_update_queue.get(timeout=1)
                # Update the GUI from the main thread. `update_gui` is called after 100 ms
                self.root.after(100, self.update_gui, update)
            except queue.Empty:
                pass

    # Update the GUI based on the received update. It is done from the main thread
    # TODO: check that it is really performed by the main thread
    def update_gui(self, update):
        if len(update) == 7:
            # Update the Download tree
            filename, size, mirror, progress, status, speed, eta = update
            self.update_download_status(filename, size, mirror, progress, status, speed, eta)
        else:
            # Update the Log text
            msg, log_level = update
            self.update_log_table(msg, log_level)

    def create_widgets(self):
        # Search Entry and Button
        searchFrame = tk.LabelFrame(self.root, text='Search')
        searchFrame.grid(row=0, column=0, padx=(15, 0), pady=(0, 0), sticky='nsew')
        self.search_entry = tk.Entry(searchFrame, width=60)
        self.search_entry.grid(row=0, column=0, padx=(5, 0), pady=(10, 0), sticky='w')
        search_button = tk.Button(searchFrame, text='Search', command=self.search_ebooks)
        search_button.grid(row=0, column=0, padx=(480, 50), pady=(10, 0))

        # Search Results Table
        columns = {'ID': 80, 'Title': 370, 'Author(s)': 255, 'Publisher': 200,
                   'Year': 50, 'Language': 120, 'Pages': 50, 'Size': 50,
                   'Extension': 50}
        self.search_tree = self.create_table(searchFrame, columns, height=12)
        self.search_tree.grid(row=1, column=0, columnspan=3, padx=(5, 25), pady=(10, 0), sticky='nsew')
        # Horizontal bar
        horizscrollbar = tk.Scrollbar(searchFrame, orient='horizontal', command=self.search_tree.xview)
        horizscrollbar.grid(row=2, column=0, columnspan=3, padx=(5, 25), pady=(4, 1), sticky='ew')
        # Vertical bar
        verticscrollbar = tk.Scrollbar(searchFrame, orient='vertical', command=self.search_tree.yview)
        verticscrollbar.grid(row=1, column=2, padx=(192, 0), pady=(10, 0), sticky='ns')
        self.search_tree.configure(xscrollcommand=horizscrollbar.set, yscrollcommand=verticscrollbar.set)
        # Buttons
        self.search_tree.bind('<ButtonRelease-1>', self.select_items_from_search_tree)
        self.search_tree.bind('<Button-2>', self.show_popup_menu_for_search_table)

        # Create a label and combobox for page number selection
        label_page_number = tk.Label(searchFrame, text="Page number:")
        label_page_number.grid(row=3, column=2, padx=(0, 125), pady=10, sticky="e")

        # Combobox showing list of pages
        page_numbers = []
        self.page_var = tk.StringVar()
        page_combobox = ttk.Combobox(searchFrame, textvariable=self.page_var, values=page_numbers, state="readonly", width=9)
        page_combobox.set("Select Page")
        page_combobox.grid(row=3, column=2, padx=(0, 20), pady=10, sticky="e")

        self.page_var.trace_add("write", self.on_page_select)

        # Download Queue Table
        columns = {'Filename': 455, 'Size': 75, 'Mirror': 50, 'Progress': 60, 'Status': 100, 'Speed': 90, 'ETA': 50}
        downloadFrame = tk.LabelFrame(self.root, text='Download')
        downloadFrame.grid(row=1, column=0, padx=(15, 0), pady=(10, 0), sticky='nsw')
        self.download_tree = self.create_table(downloadFrame, columns, anchor='center', height=12)
        self.download_tree.column('Filename', anchor='w')
        self.download_tree.grid(row=0, column=0, padx=(5, 25), pady=(10, 0), sticky='nsew')
        self.download_tree.bind('<ButtonRelease-1>', self.select_items_from_download_tree)
        self.download_tree.bind('<Button-2>', self.show_popup_menu_for_download_table)
        # Horizontal bar
        horizscrollbar = tk.Scrollbar(downloadFrame, orient='horizontal', command=self.download_tree.xview)
        horizscrollbar.grid(row=1, column=0, padx=(5, 25), pady=(4, 5), sticky='ew')
        # Vertical bar
        verticscrollbar = tk.Scrollbar(downloadFrame, orient='vertical', command=self.download_tree.yview)
        verticscrollbar.grid(row=0, column=0, padx=(894, 0), pady=(10, 0), sticky='ns')
        self.download_tree.configure(xscrollcommand=horizscrollbar.set, yscrollcommand=verticscrollbar.set)

        # Logging text with horizontal scrollbar
        loggingFrame = tk.LabelFrame(self.root, text='Logging')
        loggingFrame.grid(row=1, column=0, padx=(960, 0), pady=(10, 0), sticky='nsew')
        self.logging_text = tk.Text(loggingFrame, wrap='none', width=40, height=18)
        self.logging_text.grid(row=0, column=0, padx=(0, 0), pady=(5, 0), sticky='nsew')
        # Horizontal bar
        horizscrollbar = tk.Scrollbar(loggingFrame, orient='horizontal', command=self.logging_text.xview)
        horizscrollbar.grid(row=1, column=0, padx=(3, 3), pady=(0, 0), sticky='ew')
        # Vertical bar
        verticscrollbar = tk.Scrollbar(loggingFrame, orient='vertical', command=self.logging_text.yview)
        verticscrollbar.grid(row=0, column=1, padx=(0, 5), pady=(8, 5), sticky='ns')
        self.logging_text.configure(xscrollcommand=horizscrollbar.set, yscrollcommand=verticscrollbar.set)

        # Create a right-click pop-up menu
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.update_toggle_label()  # Initialize the label

        # Explicitly set the label for "Toggle Logging" during initialization
        self.context_menu.add_command(label=self.toggle_label.get(), command=self.toggle_logging)
        self.context_menu.add_separator()

        for var, level_name in self.log_levels:
            self.context_menu.add_checkbutton(label=level_name, variable=var,
                                              command=lambda level=level_name: self.set_logging_level(level))
            # Set default check state
            if level_name == DEFAULT_LOGGING_LEVEL:
                var.set(1)

        self.context_menu.add_separator()
        self.context_menu.add_command(label="Clear All Logs", command=self.clear_all_logs)

        # Bind the right-click event to the text widget
        # TODO: on Linux it is <Button-3>, on macOS it is <Button-2>
        self.logging_text.bind("<Button-2>", self.show_popup_menu_for_logging_text)

        # Clear all Button
        # clear_downloads_button = tk.Button(self.root, text='Clear All Downloads', command=self.clear_downloads)
        # clear_downloads_button.grid(row=6, column=0, padx=5, pady=0, sticky='nsew')

        # Configure column weights to adjust spacing
        # Configure row and column weights to make tables expand
        searchFrame.rowconfigure(0, weight=1)
        searchFrame.rowconfigure(1, weight=1)
        searchFrame.columnconfigure(0, weight=1)
        searchFrame.columnconfigure(1, weight=1)

        downloadFrame.rowconfigure(0, weight=1)
        downloadFrame.rowconfigure(1, weight=1)
        downloadFrame.columnconfigure(0, weight=1)
        downloadFrame.columnconfigure(1, weight=1)

        loggingFrame.rowconfigure(0, weight=1)
        loggingFrame.rowconfigure(1, weight=1)
        loggingFrame.columnconfigure(0, weight=1)
        loggingFrame.columnconfigure(1, weight=1)

    # TODO: `args` not used
    def on_page_select(self, *args):
        selected_page = self.page_var.get()
        try:
            # Skip "Select Page"
            selected_page = int(selected_page)

            if not self.first_search:
                self.search_ebooks(selected_page, from_combobox=True)
            else:
                self.first_search = False
        except ValueError:
            pass

    # Ref.: https://github.com/carterprince/libby/blob/main/libby
    def search_ebooks(self, page=1, from_combobox=False):
        # Clear existing search results
        for item in self.search_tree.get_children():
            self.search_tree.delete(item)

        if from_combobox and self.url in self.book_ids_per_urls and page in self.book_ids_per_urls[self.url]:
            book_ids = self.book_ids_per_urls[self.url][page]["book_ids"]
        else:
            if page == 1:
                # Clear combobox
                # TODO: don't hardcode `!combobox`
                self.root.children['!labelframe'].children['!combobox']['values'] = []
                self.root.children['!labelframe'].children['!combobox'].set("Select Page")

                # Perform search and display results
                # TODO: lowercase query, e.g. 'paul dirac' == 'Paul Dirac'
                self.query = self.search_entry.get()
                logger.info(f"Query: '{self.query}'")

                # Search in fields (Columns): Title, Author(s), Series, Year, ISBN
                # Search in Objects: Files
                # Search in Topics: Libgen and Fiction
                # Order: Year
                # Order mode: DESC
                # Results: 25
                # Goggle mode: ON
                # Search in files: All
                #
                # NOTE: Advanced search mode (Google mode), allows you to set more precise search terms:
                # quotes "", mask *, excluding words - (minus)
                self.url = f"{self.domain}/index.php?req={requests.utils.quote(self.query)}" \
                           "&columns%5B%5D=t&columns%5B%5D=a&columns%5B%5D=s&columns%5B%5D=y&" \
                           "columns%5B%5D=i&objects%5B%5D=f&topics%5B%5D=l&topics%5B%5D=f&" \
                           f"curtab=f&order=year&ordermode=desc&res={self.results_per_page}&" \
                           f"gmode=on&filesuns=all"
                self.book_ids_per_urls.setdefault(self.url, {})
                logger.debug(self.url)
            else:
                pass

            assert self.url
            url = self.url + f"&page={page}"

            if self.url in self.book_ids_per_urls and page in self.book_ids_per_urls[self.url]:
                book_ids = self.book_ids_per_urls[self.url][page]["book_ids"]
            else:

                def retrieve_search_results():
                    # We are going to do some work
                    global RESPONSE
                    start = time.time()
                    RESPONSE = requests.get(url, headers=self.headers)
                    duration = time.time() - start
                    logger.info(f"It took {int(duration)}s")

                logger.info(f"Retrieving results for page {page}...")
                t = threading.Thread(target=retrieve_search_results, daemon=True)
                t.start()

                # Create the loading screen
                loading_screen = tk.Toplevel(self.root)
                loading_screen.title("Wait")
                loading_label = tk.Label(loading_screen, text=f"Retrieving results from {self.domain}/index.php ...")
                loading_label.pack(padx=0, pady=5)

                # Calculate the center position for the popup window
                # TODO: not centered
                screen_width = self.root.winfo_screenwidth()
                screen_height = self.root.winfo_screenheight()
                popup_width = 350  # Set the width of your popup window
                popup_height = 50  # Set the height of your popup window
                x = (screen_width - popup_width) // 3
                y = (screen_height - popup_height) // 3
                # Set the geometry of the popup window to the center position
                loading_screen.geometry(f"{popup_width}x{popup_height}+{x}+{y}")

                # While the thread is alive
                while t.is_alive():
                    # Update the root so it will keep responding
                    self.root.update()

                loading_screen.destroy()

                soup = BeautifulSoup(RESPONSE.text, "html.parser")
                table = soup.find(id="tablelibgen")
                if not table:
                    logger.info(f"No results found for '{self.query}'")
                    # TODO: code factorization
                    logger.info("*" * 30)
                    return

                # Find number of pages
                # TODO: add try except
                nb_files_found = int(soup.find("a", {'class': 'nav-link active'}).find('span').text)
                try:
                    max_nb_files = int(soup.find("a", {'class': 'nav-link active'}).find('i').text.split()[-1])
                except AttributeError:
                    # TODO: log exception
                    # No attribute 'text', i.e. `<i>Showing the first  1000</i>` not found
                    max_nb_files = 1000
                if nb_files_found > max_nb_files:
                    nb_pages = int(math.ceil(max_nb_files/self.results_per_page))
                else:
                    nb_pages = int(math.ceil(nb_files_found/self.results_per_page))

                rows = table.select("tr")
                # TODO: describe structure of `books`
                book_ids = []
                for row in rows[1:]:
                    cells = row.select("td")
                    if len(cells) < 9:
                        # TODO: add log warning
                        continue

                    language = cells[4].get_text(strip=True)
                    if 'all' not in self.languages and language.lower() not in self.languages:
                        # TODO: add log warning
                        continue

                    extension = cells[7].get_text(strip=True)
                    if 'all' not in self.extensions and extension not in self.extensions:
                        # TODO: add log warning
                        continue

                    book_id = cells[0].find('span', {'class': "badge-secondary"}).get_text(strip=True).replace(' ', '')
                    pages = cells[5].get_text(strip=True)

                    title_tags = cells[0].find_all('a', {'data-toggle': 'tooltip'})
                    title = None
                    for title_tag in title_tags:
                        text = title_tag.get_text(strip=True)
                        if text:
                            title = text
                            break
                    if not title:
                        for title_tag in title_tags:
                            title_attr = title_tag.get('title')
                            if title_attr:
                                match = re.search(r'<br>(<.*?>)?(.*?)$', title_attr)
                                if match:
                                    title = match.group(2).strip()
                                    break
                    # TODO: add as option
                    full_titles = True
                    if not full_titles:
                        if ": " in title:
                            title = title.split(": ")[0]
                        elif " - " in title:
                            title = title.split(" - ")[0]

                    author = cells[1].get_text(strip=True)
                    publisher = cells[2].get_text(strip=True)
                    # TODO: add as option
                    all_authors = True
                    if not all_authors:
                        author = get_first_author(author)
                        publisher = get_first_author(publisher)

                    year = cells[3].get_text(strip=True)
                    size = cells[6].get_text(strip=True)

                    mirrors = {}
                    md5 = None
                    for i, tag in enumerate(cells[8].find_all('a')):
                        if tag["href"]:
                            url = tag['href']
                            if tag["href"].startswith('/ads'):
                                k = 1
                                # TODO: use `requests` to build url
                                url = f"{self.domain}{url}"
                                md5 = tag["href"].strip("/ads")
                            elif "library." in tag["href"]:
                                k = 2
                            elif "annas-archive" in tag["href"]:
                                k = 3
                            elif "sci-hub" in tag["href"]:
                                k = 4
                            elif "bookfi" in tag["href"]:
                                # bookfi.net doesn't work anymore
                                k = 5
                            else:
                                # TODO: log this case as an unsupported mirror
                                continue
                            mirrors[k] = url
                    if not mirrors:
                        if self.get_logging_level() == 'Debug':
                            print("HTML:\n", cells[8].prettify(), "\n---\n")
                        logger.warning("Could not find the mirror element. "
                                       "Please check the selector or the "
                                       "mirror index.")
                        logger.warning("*" * 30)
                        continue
                    if md5:
                        book_data = {
                                "book_id": book_id,
                                "title": unescape(title),
                                "author": unescape(author),
                                "publisher": unescape(publisher),
                                "year": unescape(year),
                                "language": unescape(language),
                                "pages": unescape(pages),
                                "size": unescape(size),
                                "extension": unescape(extension),
                                "mirrors": mirrors,
                                "md5": md5
                        }
                        self.books.setdefault(book_id, book_data)
                        book_ids.append(book_id)
                    else:
                        # TODO: add log warning
                        continue

                if not book_ids:
                    logger.info(f"No results found for '{self.query}'")
                    logger.info("*" * 30)
                    # TODO: return code
                    return

                # TODO: explain solution
                if not self.first_search and page == 1:
                    self.first_search = True
                self.book_ids_per_urls[self.url].setdefault(page, {"book_ids": book_ids,
                                                                   "nb_files_found": nb_files_found,
                                                                   "max_nb_files": max_nb_files,
                                                                   "nb_pages": nb_pages})

                logger.info(f"Number of files found: {nb_files_found}")
                if nb_files_found > max_nb_files:
                    logger.info(f"Showing the first {max_nb_files}")
                logger.info(f"Number of pages: {nb_pages}")
                logger.info(f"Number of books shown: {len(book_ids)}")
                logger.info("*"*30)

            if page == 1:
                # TODO: don't call the combo box like that
                nb_pages = self.book_ids_per_urls[self.url][page]["nb_pages"]
                self.root.children['!labelframe'].children['!combobox']['values'] = list(range(1, nb_pages + 1))

        for book_id in book_ids:
            book = self.books[book_id]
            self.search_tree.insert("", "end", values=list(book.values())[:9])

        # TODO: don't call the combo box like that
        self.root.children['!labelframe'].children['!combobox'].set(page)

    # TODO: `event` not used
    def select_items_from_search_tree(self, event):
        self.selected_items_from_search_tree.clear()
        self.selected_items_from_search_tree = set(self.search_tree.selection())

    # TODO: `event` not used
    def select_items_from_download_tree(self, event):
        self.selected_items_from_download_tree.clear()
        self.selected_items_from_download_tree = set(self.download_tree.selection())

    @staticmethod
    def create_table(parent, columns, anchor='w', height=None):
        tree = ttk.Treeview(parent, columns=list(columns.keys()), show='headings', height=height)
        for col_name, col_width in columns.items():
            tree.heading(col_name, text=col_name)
            tree.column(col_name, width=col_width, anchor=anchor, stretch=0)
        return tree

    # TODO: change function name
    def thread_func(self, item, mirror):
        # TODO: only retrieve info that are needed
        book_id, title, authors, publisher, year, language, pages, size, ext = self.search_tree.item(item, "values")

        # Ref.: https://github.com/carterprince/libby/blob/main/libby
        nb_retries1 = 0
        nb_retries2 = 0
        mirror_soup = None
        download_url = None
        next_step = False
        while nb_retries1 <= self.max_retries and nb_retries2 <= self.max_retries:
            if not next_step:
                mirror_url = self.books[book_id]['mirrors'][mirror]
                # TODO: catch `requests.exceptions.SSLError` e.g. 504 Gateway Time-out
                mirror_response = requests.get(mirror_url, headers=self.headers)
                if mirror_response.status_code != 200:
                    # TODO: code factorization
                    nb_retries1 += 1
                    msg = "Couldn't process mirror URL"
                    if nb_retries1 == self.max_retries:
                        logger.warning(msg + ". Will retry again.")
                        logger.debug(f"Sleeping [retry1={nb_retries1}] ...")
                        time.sleep(self.delay_between_retries)
                    else:
                        logger.warning(msg)
                else:
                    mirror_soup = BeautifulSoup(mirror_response.text, "html.parser")
                    next_step = True
            else:
                try:
                    assert mirror_soup
                    download_url = mirror_soup.find("a", string="GET")["href"].replace("\get.php", "/get.php")
                    break
                except TypeError:
                    # e.g. TypeError: 'NoneType' object is not subscriptable
                    nb_retries2 += 1
                    msg = "Couldn't find download URL"
                    if nb_retries2 == self.max_retries:
                        logger.warning(msg + ". Will retry again.")
                        logger.debug(f"Sleeping [retry2={nb_retries2}] ...")
                        time.sleep(self.delay_between_retries)
                    else:
                        logger.warning(msg)

        if nb_retries1 > self.max_retries or nb_retries2 > self.max_retries:
            logger.warning(f"Skipped mirror URL: {mirror_url}")
            return

        assert download_url
        nb_retries = 0
        download_response = None
        while nb_retries <= self.max_retries:
            download_response = requests.get(download_url, headers=self.headers, stream=True)
            if download_response.status_code != 200:
                nb_retries += 1
                msg = "Couldn't process download URL"
                if nb_retries == self.max_retries:
                    logger.warning(msg + ". Will retry again.")
                    logger.debug(f"Sleeping [retry={nb_retries}] ...")
                    time.sleep(self.delay_between_retries)
                else:
                    logger.warning(msg)
            else:
                break

        if nb_retries > self.max_retries:
            logger.warning(f"Skipped download URL [{download_response.status_code}]: {download_url}")
            return
        else:
            # TODO: necessary?
            assert download_response
            download_response.close()

        # Generate unique filename from response to download URL
        filepath = unique_filename(Path.cwd(), pyrfc6266.requests_response_to_filename(download_response))
        filename = Path(filepath).name
        logger.debug(f"Filename: {Path(filepath).name}")
        self.download_tree.insert("", "end", values=(filename, size, mirror, "0%", "Waiting"))
        self.filenames.setdefault(filename, {'book_id': book_id,
                                             'download_url': download_url})

        # TODO: use lock for reading `shared_nb_mirror1` and `shared_nb_mirror2`?
        if mirror == 1 and self.shared_nb_mirror1 > 2 or \
                mirror == 2 and self.shared_nb_mirror2 > 2:
            add_to_queue = True
        else:
            add_to_queue = False
        logger.debug(f'{self.shared_nb_mirror1} and {self.shared_nb_mirror2}')

        # Start download in a separate thread
        if not add_to_queue and self.shared_nb_threads < 6:
            th_name = f"Thread-{self.shared_nb_threads + 1}"
            thread = threading.Thread(target=self.download_ebook, args=(filename, size, mirror, th_name, download_url))
            thread.daemon = True
            thread.start()
            with self.lock_nb_threads:
                self.shared_nb_threads += 1
            logger.debug(f"Thread created: {th_name}")
            self.update_mirror_counter_with_lock(mirror, 1)
        else:
            logger.debug(f"Adding work to download queue: filename={filename} and mirror={mirror}")
            with self.lock_download_queue:
                self.shared_download_queue.append((filename, size, mirror))

    def download_selected(self, mirror):
        logger.debug(f"Downloading {len(self.selected_items_from_search_tree)} file(s) with mirror={mirror}")
        # One thread per item selected from the Search table
        for item in self.selected_items_from_search_tree:
            threading.Thread(target=self.thread_func, args=(item, mirror), daemon=True).start()

    # Worker thread
    # IMPORTANT: within a thread, you can't use `logger`, you must use `gui_update_queue` since it is the main thread
    # that is in charge of logging directly to the logs widget
    def download_ebook(self, filename, size, mirror, th_name, download_url):
        thread = threading.current_thread()
        thread.setName(th_name)
        stop = False
        self.gui_update_queue.put((f"{th_name}: starting first download "
                                   f"with filename={filename} and mirror={mirror}", "debug"))
        while True:
            self.filenames_by_threads[filename] = thread

            # TODO: code factorization
            nb_retries = 0
            download_response = None
            # Create a session
            # TODO: session necessary?
            session = requests.Session()
            while nb_retries <= self.max_retries:
                download_response = session.get(download_url, headers=self.headers, stream=True)
                if download_response.status_code != 200:
                    nb_retries += 1
                    msg = "Couldn't process download URL"
                    if nb_retries == self.max_retries:
                        self.gui_update_queue.put((f"{th_name}: {msg}. Will retry again.", "warning"))
                        self.gui_update_queue.put((f"{th_name}: sleeping [retry={nb_retries}] ...", "debug"))
                        time.sleep(self.delay_between_retries)
                    else:
                        self.gui_update_queue.put((f"{th_name}: {msg}", "warning"))
                    time.sleep(self.delay_between_retries)
                else:
                    break

            percentage_completion = 0
            total_size = ""
            size_downloaded = "0 MB"
            bytes_so_far = [0]
            incomplete = False
            if nb_retries > self.max_retries:
                self.gui_update_queue.put(
                    (f"{th_name}: skipped download URL [{download_response.status_code}]: {download_url}", "warning"))
                stop = True
            else:
                # TODO: necessary?
                assert download_response
                total_size = int(download_response.headers.get('content-length', 0))
                # Check if the 'content-length' header is present and valid
                # total_size = int(download_response.headers.get('content-length', 0))
                # if 'content-length' in download_response.headers else None

                # TODO: test if file error (e.g. directory doesn't exist)
                with open(Path.cwd().joinpath(filename), "wb") as f:
                    start_time = time.time()
                    for chunk in download_response.iter_content(chunk_size=self.chunk_size):
                        f.write(chunk)
                        bytes_so_far[0] += len(chunk)

                        # Calculate percentage completion, ETA and download speed
                        # NOTE: use `bytes_so_far[0]` instead of `pbar.n` because `pbar.n` always gives 0
                        percentage_completion = (bytes_so_far[0] / total_size) * 100 if total_size is not None and total_size > 0 else 0
                        # Elapsed time in seconds
                        elapsed_time = time.time() - start_time
                        # Download speed in B/s
                        download_speed = bytes_so_far[0] / elapsed_time if elapsed_time > 0 else 0
                        eta_seconds = (total_size - bytes_so_far[0]) / download_speed if download_speed > 0 else 0
                        eta_formatted = self.format_time(eta_seconds)
                        download_speed_formatted = self.format_size(download_speed) + '/s'
                        size_downloaded = self.format_size(bytes_so_far[0])

                        self.gui_update_queue.put((filename, size_downloaded, mirror, f"{percentage_completion:.2f}%",
                                                   "Downloading", f"{download_speed_formatted}",
                                                   f"{eta_formatted}"))

                        # Stop (cancel) thread
                        with self.lock_stop_thread:
                            if th_name in self.shared_stop_thread:
                                self.gui_update_queue.put((f"{th_name}: thread will stop what it is doing", "debug"))
                                stop = True
                                self.shared_stop_thread.remove(th_name)
                                break

                        # Pause thread
                        pause = False
                        with self.lock_pause_thread:
                            if th_name in self.shared_pause_thread:
                                self.gui_update_queue.put((f"{th_name}: thread will pause what it is doing", "debug"))
                                self.gui_update_queue.put(
                                    (filename, size, mirror, f"{percentage_completion}%", "Paused", "-", "-"))
                                self.shared_pause_thread.remove(th_name)
                                pause = True

                        # Resume thread
                        if pause:
                            while True:
                                # self.gui_update_queue.put((f"{th_name}: thread will sleep", "debug"))
                                time.sleep(0.1)
                                # self.gui_update_queue.put((f"{th_name}: thread will check if it can resume", "debug"))
                                with self.lock_resume_thread:
                                    if th_name in self.shared_resume_thread:
                                        self.gui_update_queue.put(
                                            (f"{th_name}: thread will resume what it was doing", "debug"))
                                        self.shared_resume_thread.remove(th_name)
                                        break
                                # TODO: factorization, code block used above
                                with self.lock_stop_thread:
                                    if th_name in self.shared_stop_thread:
                                        self.gui_update_queue.put((f"{th_name}: thread will stop what it was doing",
                                                                   "debug"))
                                        stop = True
                                        self.shared_stop_thread.remove(th_name)
                                        break
                        if stop:
                            break

                    # Incomplete download
                    if not stop and total_size != bytes_so_far[0]:
                        # TODO IMPORTANT: add retry in this case
                        self.gui_update_queue.put((f"{th_name}: could only complete {percentage_completion:.2f}% of "
                                                   "the whole download.", "error"))
                        incomplete = True

            session.close()
            if incomplete:
                if Path.cwd().joinpath(filename).exists():
                    self.remove_file(Path.cwd().joinpath(filename))
                self.gui_update_queue.put(
                    (filename, "-", mirror, f"{percentage_completion:.2f}%", "Incomplete", "-", "-"))
            elif stop:
                if Path.cwd().joinpath(filename).exists():
                    self.remove_file(Path.cwd().joinpath(filename))
                stop = False
                self.gui_update_queue.put(
                    (filename, "-", mirror, f"{percentage_completion:.2f}%", "Canceled", "-", "-"))
            else:
                # Update status to indicate download completion
                self.gui_update_queue.put((f"{th_name}: {percentage_completion:.2f}%, {total_size}, {size_downloaded}, "
                                           f"{bytes_so_far[0]}", "warning"))
                self.gui_update_queue.put((f"{th_name}: finished downloading "
                                           "and updating status with "
                                           f"filename={filename} and {mirror}", "debug"))
                self.gui_update_queue.put((filename, size_downloaded, mirror, "100%", "Downloaded", "-", "-"))

            self.update_mirror_counter_with_lock(mirror, -1)
            self.gui_update_queue.put((f"{th_name}: thread waiting for work...", "debug"))
            while True:
                # Get the next ebook to download from the top of the download queue
                # i.e. the least recent ebook added
                if self.shared_download_queue:
                    with self.lock_download_queue:
                        _, _, mirror = self.shared_download_queue[0]
                    # TODO: use lock for reading shared_nb_mirror1 and shared_nb_mirror2?
                    with self.get_mirror_lock(mirror):
                        if mirror == 1 and self.shared_nb_mirror1 < 3 or mirror == 2 and self.shared_nb_mirror2 < 3:
                            filename, size, mirror = self.shared_download_queue.pop(0)
                            self.update_mirror_counter_without_lock(mirror, 1)
                            break
                else:
                    time.sleep(0.1)
            self.gui_update_queue.put((f"{th_name}: starting new download with "
                                       f"filename={filename} and mirror={mirror}", "debug"))

    @staticmethod
    def format_size(size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0

    @staticmethod
    def format_time(seconds):
        intervals = [('days', 86400), ('hrs', 3600), ('mins', 60), ('secs', 1)]
        result = []
        for name, count in intervals:
            value = seconds // count
            if value:
                result.append(f"{int(value)} {name}")
            seconds %= count
        return ', '.join(result)

    def remove_file(self, file_path):
        # Ref.: https://stackoverflow.com/a/42641792
        try:
            os.remove(file_path)
            return 0
        except OSError as e:
            self.gui_update_queue.put((f"{e.filename} - {e.strerror}.", "error"))
            return 1

    # Update mirror counter with the appropriate lock
    def update_mirror_counter_with_lock(self, mirror, value):
        with self.get_mirror_lock(mirror):
            self.update_mirror_counter_without_lock(mirror, value)

    def update_mirror_counter_without_lock(self, mirror, value):
        if mirror == 1:
            self.shared_nb_mirror1 += value
        else:
            self.shared_nb_mirror2 += value

    # Return the lock associated with the mirror
    def get_mirror_lock(self, mirror):
        if mirror == 1:
            return self.lock_mirror1
        else:
            return self.lock_mirror2

    def update_download_status(self, filename, size, mirror, progress, status, speed="", eta=""):
        # Update status and progress in the download queue table
        for child in self.download_tree.get_children():
            if self.download_tree.item(child, 'values')[0] == filename:
                try:
                    self.download_tree.item(child, values=(filename, size, mirror, progress, status, speed, eta))
                except:
                    # TODO: remove try-except block
                    ipdb.set_trace()
                break

    @staticmethod
    def update_log_table(msg, log_level):
        logger.__getattribute__(log_level)(msg)

    def show_popup_menu_for_search_table(self, event):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label='Download with Mirror 1 (libgen)', command=lambda: self.download_selected(1))
        menu.add_command(label='Download with Mirror 2 (libgen.is)', command=lambda: self.download_selected(2))
        menu.post(event.x_root, event.y_root)

    def show_popup_menu_for_download_table(self, event):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label='Pause', command=self.pause_download)
        menu.add_command(label='Resume', command=self.resume_download)
        menu.add_command(label='Cancel', command=self.cancel_download)
        menu.add_command(label='Remove from list', command=self.remove_download)
        menu.add_command(label='Show in Finder', command=self.show_in_finder)
        menu.post(event.x_root, event.y_root)

    def show_popup_menu_for_logging_text(self, event):
        self.update_toggle_label()  # Ensure the label is up-to-date
        self.context_menu.post(event.x_root, event.y_root)

    def toggle_logging(self):
        current_state = self.toggle_var.get()
        self.toggle_var.set(1 - current_state)
        self.update_toggle_label()

    def update_toggle_label(self):
        current_state = self.toggle_var.get()
        label = "Turn off logging" if current_state == 0 else "Turn on logging"
        self.context_menu.entryconfig(0, label=label)  # Update the label of the first entry
        if current_state:
            if logger.handlers:
                logger.handlers = []
                self.logging_text.insert("end", "The logging is turned off" + '\n')
        else:
            if self.logger_is_setup and not logger.handlers:
                level = self.get_logging_level()
                logger.setLevel(level)
                # handler = TKTextHandler(self.logging_text)
                handler = logging.StreamHandler()
                formatter = logging.Formatter('%(levelname)s - %(message)s')
                handler.setFormatter(formatter)
                handler.setLevel(level)

                # Add the handler to the logger
                logger.addHandler(handler)

                self.logging_text.insert("end", "The logging is turned on" + '\n')

    def set_logging_level(self, level):
        for var, level_name in self.log_levels:
            var.set(1 if level_name == level else 0)
        # messagebox.showinfo("Info", f"Set logging level to: {level}")
        if logger.handlers:
            logger.setLevel(level.upper())
            logger.handlers[0].setLevel(level.upper())
        self.logging_text.insert("end", f"Logging level set to '{level}'" + '\n')

    def get_logging_level(self):
        for var, level_name in self.log_levels:
            if var.get():
                return level_name.upper()
        return None

    def clear_all_logs(self):
        self.logging_text.delete("1.0", tk.END)

    def pause_download(self):
        if self.download_tree.get_children() == ():
            logger.info("Download queue is empty!")
        elif self.selected_items_from_download_tree == set():
            logger.info("No selected rows!")
        else:
            logger.debug("Pause Download")
            for item in self.selected_items_from_download_tree:
                values = self.download_tree.item(item, 'values')
                filename = values[0]
                status = values[4]
                if status == 'Downloading':
                    logger.debug(f"Pausing {filename}")
                    if self.filenames_by_threads.get(filename, False):
                        thread = self.filenames_by_threads[filename]
                        with self.lock_pause_thread:
                            self.shared_pause_thread.add(thread.name)
                    else:
                        logger.warning(f"{filename} couldn't be paused!")
                else:
                    logger.debug(f"{filename}: not downloading")
                    logger.debug(f"{filename}: its status='{status}'")
            self.selected_items_from_download_tree.clear()
            # Remove highlighting
            self.download_tree.selection_remove(self.download_tree.selection())

    def resume_download(self):
        if self.download_tree.get_children() == ():
            logger.info("Download queue is empty!")
        elif self.selected_items_from_download_tree == set():
            logger.info("No selected rows!")
        else:
            logger.debug("Resume Download")
            for item in self.selected_items_from_download_tree:
                values = self.download_tree.item(item, 'values')
                filename = values[0]
                status = values[4]
                if status == 'Paused':
                    logger.debug(f"Resuming {filename}")
                    if self.filenames_by_threads.get(filename, False):
                        thread = self.filenames_by_threads[filename]
                        with self.lock_resume_thread:
                            self.shared_resume_thread.add(thread.name)
                    else:
                        logger.warning(f"{filename} couldn't be resumed!")
                else:
                    logger.debug(f"{filename}: not paused")
                    logger.debug(f"{filename}: its status='{status}'")
            self.selected_items_from_download_tree.clear()
            # Remove highlighting
            self.download_tree.selection_remove(self.download_tree.selection())

    def cancel_download(self):
        if self.download_tree.get_children() == ():
            logger.info("Download queue is empty!")
        elif self.selected_items_from_download_tree == set():
            logger.info("No selected rows!")
        else:
            logger.debug("Cancel items from the Download queue")
            for item in self.selected_items_from_download_tree:
                values = self.download_tree.item(item, 'values')
                filename = values[0]
                status = values[4]
                if status in ['Downloading', 'Paused']:
                    logger.debug(f"Canceling {filename}")
                    if self.filenames_by_threads.get(filename, False):
                        thread = self.filenames_by_threads[filename]
                        with self.lock_stop_thread:
                            self.shared_stop_thread.add(thread.name)
                        del self.filenames_by_threads[filename]
                    else:
                        logger.warning(f"{filename} couldn't be canceled!")
                else:
                    logger.debug(f"{filename}: not downloading")
                    logger.debug(f"{filename}: its status='{status}'")
            self.selected_items_from_download_tree.clear()
            # Remove highlighting
            self.download_tree.selection_remove(self.download_tree.selection())

    def clear_downloads(self):
        if self.download_tree.get_children() == ():
            logger.debug("Download queue is already empty!")
        else:
            logger.debug("Clear downloads")
            for child in self.download_tree.get_children():
                values = self.download_tree.item(child, 'values')
                filename = values[0]
                status = values[4]
                if status != 'Downloading':
                    logger.debug(f"Removing {filename}")
                    if self.filenames_by_threads.get(filename, False):
                        del self.filenames_by_threads[filename]
                    self.download_tree.delete(child)
                else:
                    logger.debug(f"{filename}: it can't be removed because its download has not completed")
                    logger.debug(f"{filename}: its status='{status}'")

    def remove_download(self):
        if self.download_tree.get_children() == ():
            logger.info("Download queue is empty!")
        elif self.selected_items_from_download_tree == set():
            logger.info("No selected rows!")
        else:
            logger.debug("Remove items from the Download queue")
            for item in self.selected_items_from_download_tree:
                values = self.download_tree.item(item, "values")
                filename = values[0]
                status = values[4]
                if status not in ["Downloading", "Paused"]:
                    logger.debug(f"Removing {filename}")
                    if self.filenames_by_threads.get(filename, False):
                        del self.filenames_by_threads[filename]
                    self.download_tree.delete(item)
                else:
                    logger.debug(f"{filename}: it can't be removed because its download has not completed")
                    logger.debug(f"{filename}: its status='{status}'")
            self.selected_items_from_download_tree.clear()
            # Remove highlighting
            self.download_tree.selection_remove(self.download_tree.selection())

    def show_in_finder(self):
        logger.debug("Show in Finder")


if __name__ == "__main__":
    root = tk.Tk()
    app = EbookDownloader(root)
    root.mainloop()
