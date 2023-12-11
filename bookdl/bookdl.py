import logging
import queue
import sqlite3
import threading
import tkinter as tk
import time

from tkinter import messagebox, ttk

import ipdb

logger = logging.getLogger("bookdl")


class TKTextHandler(logging.Handler):
    def __init__(self, tktext):
        super().__init__()
        self.tktext = tktext

    def emit(self, record):
        msg = self.format(record)
        self.tktext.insert("end", msg+'\n')


class EbookDownloader:
    def __init__(self, root):
        self.root = root
        self.root.title("Ebook Downloader")
        self.search_entry = None
        # TODO: table instead of tree
        self.search_tree = None
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
        self.filenames = set()
        self.gui_update_queue = queue.Queue()
        self.nb_threads = 0
        self.filenames_by_threads = {}
        self.shared_nb_mirror1 = 0
        self.shared_nb_mirror2 = 0
        self.shared_download_queue = []
        self.shared_stop_thread = set()

        # Separate locks for different resources
        self.lock_mirror1 = threading.Lock()
        self.lock_mirror2 = threading.Lock()
        self.lock_download_queue = threading.Lock()
        self.lock_stop_thread = threading.Lock()

        # Create and connect to SQLite database
        self.conn = sqlite3.connect("ebooks.db")
        self.cursor = self.conn.cursor()

        # Create ebook table if not exists
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS ebooks (
                id INTEGER PRIMARY KEY,
                Authors TEXT,
                Title TEXT,
                Publisher TEXT,
                YEAR TEXT,
                Pages TEXT,
                Language TEXT,
                Size TEXT,
                Extension TEXT
            )
        ''')
        if False:
            self.cursor.execute('''
                INSERT INTO ebooks (
                    Authors,
                    Title,
                    Publisher,
                    YEAR,
                    Pages,
                    Language,
                    Size,
                    Extension
                ) VALUES 
                ('Author1', 'Book1', 'Publisher1', '2001', '101', 'English', '1 Mb', 'pdf'),
                ('Author2', 'Book2', 'Publisher2', '2002', '102', 'English', '2 Mb', 'zip'),
                ('Author3', 'Book3', 'Publisher3', '2003', '103', 'English', '3 Mb', 'epub'),
                ('Author4', 'Book4', 'Publisher1', '2001', '101', 'English', '1 Mb', 'pdf'),
                ('Author5', 'Book5', 'Publisher2', '2002', '102', 'English', '2 Mb', 'zip'),
                ('Author6', 'Book6', 'Publisher3', '2003', '103', 'English', '3 Mb', 'epub'),
                ('Author7', 'Book7', 'Publisher1', '2001', '101', 'English', '1 Mb', 'pdf'),
                ('Author8', 'Book8', 'Publisher2', '2002', '102', 'English', '2 Mb', 'zip'),
                ('Author9', 'Book9', 'Publisher3', '2003', '103', 'English', '3 Mb', 'epub'),
                ('Author10', 'Book10', 'Publisher1', '2001', '101', 'English', '1 Mb', 'pdf'),
                ('Author11', 'Book11', 'Publisher2', '2002', '102', 'English', '2 Mb', 'zip'),
                ('Author12', 'Book12', 'Publisher3', '2003', '103', 'English', '3 Mb', 'epub'),
                ('Author13', 'Book13', 'Publisher1', '2001', '101', 'English', '1 Mb', 'pdf'),
                ('Author14', 'Book14', 'Publisher2', '2002', '102', 'English', '2 Mb', 'zip'),
                ('Author15', 'Book15', 'Publisher3', '2003', '103', 'English', '3 Mb', 'epub');
            ''')
        self.conn.commit()

        # Start a separate thread for GUI updates
        threading.Thread(target=self.gui_update_thread, daemon=True).start()

        # Create GUI elements
        self.logger_is_setup = False
        self.create_widgets()

        self.setup_logger()
        self.logger_is_setup = True

    def setup_logger(self):
        logger.setLevel(logging.DEBUG)

        handler = TKTextHandler(self.logging_text)
        # '%(asctime)s - %(levelname)s - %(message)s'
        formatter = logging.Formatter('%(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        handler.setLevel(logging.DEBUG)

        # Add the handler to the logger
        logger.addHandler(handler)

    def gui_update_thread(self):
        while True:
            try:
                # Get updates from the queue
                update = self.gui_update_queue.get(timeout=1)
                # Update the GUI from the main thread
                self.root.after(0, self.update_gui, update)
            except queue.Empty:
                pass

    # Update the GUI based on the received update. It is done from the main thread
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
        self.search_entry = tk.Entry(self.root, width=30)
        self.search_entry.grid(row=0, column=0, padx=(10, 0), pady=10, sticky='w')
        search_button = tk.Button(self.root, text='Search', command=self.search_ebooks)
        search_button.grid(row=0, column=0, padx=(0, 40), pady=10)

        # Search Results Table
        columns = {'Author(s)': 250, 'Title': 350, 'Publisher': 200, 'Year': 50,
                   'Pages': 50, 'Language': 100, 'Size': 50, 'Extension': 50}
        self.search_tree = self.create_table(columns)
        self.search_tree.grid(row=1, column=0, columnspan=2, padx=10, pady=10, sticky='nsew')
        self.search_tree.bind('<ButtonRelease-1>', self.select_items_from_search_tree)
        self.search_tree.bind('<Button-2>', self.show_popup_menu_for_search_table)

        # Download Queue Table
        columns = {'Name': 350, 'Size': 50, 'Mirror': 55, 'Progress': 50, 'Status': 100, 'Speed': 50, 'ETA': 50}
        self.download_tree = self.create_table(columns)
        self.download_tree.grid(row=2, column=0, padx=10, pady=10, sticky='nsew')
        self.download_tree.bind('<ButtonRelease-1>', self.select_items_from_download_tree)
        self.download_tree.bind('<Button-2>', self.show_popup_menu_for_download_table)

        # Logging text with horizontal scrollbar
        self.logging_text = tk.Text(self.root, wrap='none', width=40, height=10)
        scrollbar = tk.Scrollbar(self.root, orient='horizontal', command=self.logging_text.xview)
        self.logging_text.configure(xscrollcommand=scrollbar.set, yscrollcommand=None)
        self.logging_text.grid(row=2, column=1, padx=10, pady=10, sticky='nsew')
        scrollbar.grid(row=3, column=1, padx=10, pady=10, sticky='ew')

        # Create a right-click pop-up menu
        self.context_menu = tk.Menu(root, tearoff=0)
        self.update_toggle_label()  # Initialize the label

        # Explicitly set the label for "Toggle Logging" during initialization
        self.context_menu.add_command(label=self.toggle_label.get(), command=self.toggle_logging)
        self.context_menu.add_separator()

        for var, level_name in self.log_levels:
            self.context_menu.add_checkbutton(label=level_name, variable=var,
                                              command=lambda level=level_name: self.set_logging_level(level))
            # Set default check state
            if level_name == "Debug":
                var.set(1)

        # Bind the right-click event to the text widget
        # TODO: on Linux it is <Button-3>, on macOS it is <Button-2>
        self.logging_text.bind("<Button-2>", self.show_popup_menu_for_logging_text)

        # TODO: remove next commented code
        # Logging Table
        """
        columns = {'Log': 350}
        self.logging_tree = self.create_table(columns, anchor='w')
        self.logging_tree.grid(row=2, column=1, padx=10, pady=10, sticky="nsew")

        # Horizontal Scrollbar
        horizontal_scrollbar = ttk.Scrollbar(self.root, orient='horizontal', command=self.logging_tree.xview)
        self.logging_tree.configure(xscrollcommand=horizontal_scrollbar.set)
        horizontal_scrollbar.grid(row=3, column=1, padx=10, pady=10, sticky="ew")
        """

        # Clear all Button
        clear_all_button = tk.Button(self.root, text='Clear all', command=self.clear_all)
        clear_all_button.grid(row=3, column=0, padx=5, pady=0, sticky='nsew')

        # Configure column weights to adjust spacing
        # Configure row and column weights to make tables expand
        self.root.rowconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=1)

    def search_ebooks(self):
        # Clear existing search results
        for item in self.search_tree.get_children():
            self.search_tree.delete(item)

        # Perform search and display results
        search_term = self.search_entry.get()
        self.cursor.execute("SELECT * FROM ebooks WHERE Title LIKE ? OR Authors LIKE ?",
                            ("%" + search_term + "%", "%" + search_term + "%"))
        results = self.cursor.fetchall()

        for row in results:
            # Don't include the first column which is the row id
            self.search_tree.insert("", "end", values=row[1:])

    # TODO: `event` not used
    def select_items_from_search_tree(self, event):
        self.selected_items_from_search_tree.clear()
        self.selected_items_from_search_tree = set(self.search_tree.selection())

    # TODO: `event` not used
    def select_items_from_download_tree(self, event):
        self.selected_items_from_download_tree.clear()
        self.selected_items_from_download_tree = set(self.download_tree.selection())

    def create_table(self, columns, anchor='center'):
        tree = ttk.Treeview(self.root, columns=list(columns.keys()), show='headings')
        for col_name, col_width in columns.items():
            tree.heading(col_name, text=col_name)
            tree.column(col_name, width=col_width, anchor=anchor)
        return tree

    def download_selected(self, mirror):
        logger.debug(f"Downloading {len(self.selected_items_from_search_tree)} file(s) with {mirror}")
        for item in self.selected_items_from_search_tree:
            i = 1
            # TODO: only retrieve info that is needed
            authors, title, publisher, year, pages, language, size, ext = self.search_tree.item(item, "values")
            # TODO: use filename instead of title
            filename = title
            while filename in self.filenames:
                filename = f"{title} ({i})"
                i += 1
            self.filenames.add(filename)
            self.download_tree.insert("", "end", values=(filename, size, mirror, "0%", "Waiting"))

            # TODO: use lock for reading shared_nb_mirror1 and shared_nb_mirror2?
            if mirror == 'mirror1' and self.shared_nb_mirror1 == 3 or \
                    mirror == 'mirror2' and self.shared_nb_mirror2 == 3:
                add_to_queue = True
            else:
                add_to_queue = False

            # Start download in a separate thread
            if not add_to_queue and self.nb_threads < 6:
                th_name = f"Thread-{self.nb_threads + 1}"
                thread = threading.Thread(target=self.download_ebook, args=(filename, size, mirror, th_name))
                self.filenames_by_threads[filename] = thread
                thread.daemon = True
                thread.start()
                self.nb_threads += 1
                logger.debug(f"Thread created: {th_name}")

                # Update mirror counter without holding the lock
                self.update_mirror_counter_with_lock(mirror, 1)
            else:
                logger.debug(f"Adding work to download queue: filename={filename} and {mirror}")
                with self.lock_download_queue:
                    self.shared_download_queue.append((filename, size, mirror))

    # Worker thread
    def download_ebook(self, filename, size, mirror, th_name):
        # th_id = threading.current_thread().ident
        threading.current_thread().setName(th_name)
        stop = False
        self.gui_update_queue.put((f"{th_name}: starting first download "
                                   f"with filename={filename} and {mirror}", "debug"))
        while True:
            # Simulate download progress
            for progress in range(1, 101):
                time.sleep(0.1)
                eta = 100 - progress
                self.gui_update_queue.put((filename, size, mirror, f"{progress}%", "Downloading", "1 Mb/s", f"{eta} s"))
                with self.lock_stop_thread:
                    if th_name in self.shared_stop_thread:
                        self.gui_update_queue.put((f"{th_name}: thread will stop what it is doing", "debug"))
                        stop = True
                        self.shared_stop_thread.remove(th_name)
                        break
            if not stop:
                # Update status to indicate download completion
                self.gui_update_queue.put((f"{th_name}: finished downloading "
                                           "and updating status with "
                                           f"filename={filename} and {mirror}", "debug"))
                self.gui_update_queue.put((filename, size, mirror, "100%", "Downloaded", "-", "-"))
            else:
                stop = False
            self.update_mirror_counter_with_lock(mirror, -1)
            self.gui_update_queue.put((f"{th_name}: thread waiting for work...", "debug"))
            while True:
                # Get the next ebook to download from the top of the download queue
                # i.e. the least recent ebook added
                with self.lock_download_queue:
                    if self.shared_download_queue:
                        _, _, mirror = self.shared_download_queue[0]
                        # TODO: use lock for reading shared_nb_mirror1 and shared_nb_mirror2?
                        with self.get_mirror_lock(mirror):
                            if mirror == 'mirror1' and self.shared_nb_mirror1 < 3 or mirror == 'mirror2' and self.shared_nb_mirror2 < 3:
                                filename, size, mirror = self.shared_download_queue.pop(0)
                                self.update_mirror_counter_without_lock(mirror, 1)
                                break
                    else:
                        time.sleep(0.1)
            self.gui_update_queue.put((f"{th_name}: starting new download with "
                                       f"filename={filename} and {mirror}", "debug"))

    # Update mirror counter with the appropriate lock
    def update_mirror_counter_with_lock(self, mirror, value):
        with self.get_mirror_lock(mirror):
            self.update_mirror_counter_without_lock(mirror, value)

    def update_mirror_counter_without_lock(self, mirror, value):
        if mirror == 'mirror1':
            self.shared_nb_mirror1 += value
        else:
            self.shared_nb_mirror2 += value

    # Return the lock associated with the mirror
    def get_mirror_lock(self, mirror):
        if mirror == 'mirror1':
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
        menu.add_command(label='Download with Mirror 1', command=lambda: self.download_selected("mirror1"))
        menu.add_command(label='Download with Mirror 2', command=lambda: self.download_selected("mirror2"))
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
                handler = TKTextHandler(self.logging_text)
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

    def pause_download(self):
        logger.debug("Pause Download")

    def resume_download(self):
        logger.debug("Resume Download")

    def cancel_download(self):
        logger.debug("Cancel Download")

    def clear_all(self):
        logger.debug("Clear all")

    def remove_download(self):
        if self.download_tree.get_children() == ():
            logger.info("Download queue is empty!")
        elif self.selected_items_from_download_tree == set():
            logger.info("No selected rows!")
        else:
            logger.debug("Remove items from the Download queue")
            for item in self.selected_items_from_download_tree:
                values = self.download_tree.item(item, 'values')
                filename = values[0]
                status = values[4]
                if status in ["Downloaded", "Waiting"]:
                    logger.debug(f"Removing {filename}")
                    if self.filenames_by_threads.get(filename, False):
                        thread = self.filenames_by_threads[filename]
                        with self.lock_stop_thread:
                            self.shared_stop_thread.add(thread.ident)
                        del self.filenames_by_threads[filename]
                    self.download_tree.delete(item)
                else:
                    logger.debug(f"{filename}: it can't be removed because its download has not completed")
                    logger.debug(f"{filename}: its status='{status}'")
            self.selected_items_from_download_tree.clear()

    def show_in_finder(self):
        logger.debug("Show in Finder")


if __name__ == "__main__":
    root = tk.Tk()
    app = EbookDownloader(root)
    root.mainloop()
