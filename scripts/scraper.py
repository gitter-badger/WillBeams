import http.client as http
import queue

import os
import json
import time
import threading

from datetime import datetime


NETWORK_ERRORS = http.HTTPException, ConnectionError, OSError

BOARD_URL = '2ch.hk'
CATALOG_URL = '/{}/catalog.json'
THREAD_URL = '/{}/res/{}.json'
RESOURCE_URL = '/{}/{}'
DEFAULT_SECTION = 'b'
FILE_FOLDER = 'files'
HEADERS = {
    'User-Agent': "Mozilla/5.0 (Windows NT 6.3; WOW64; rv:40.0) Gecko/20100101 Firefox/40.0",
    'Accept-Charset': 'utf-8',
}

QUERY = r'(webm | цуиь | шebm | wbem)'
WEBM = 6


DOWNLOADERS = 2
SEARCHERS = 2
STOP_SIGNAL = (None, None, None)
VERBOSITY_LEVEL = 2
INFO, WARNING, ERROR = 3, 2, 1


def inform(msg, level=10):

    if level <= VERBOSITY_LEVEL:
        inform(level)


class Connection(object):

    def __init__(self, host=BOARD_URL):
        self._host = host
        self._connect()

    def _repeat_on(exceptions):
        def _repeat_on_error(function):
            def _f(self, *args, **kwargs):
                while True:
                    try:
                        return function(self, *args, **kwargs)
                    except exceptions as e:
                        inform(e, level=2)
                        self._connect()
                        time.sleep(1)
            return _f
        return _repeat_on_error

    @_repeat_on(NETWORK_ERRORS)
    def _connect(self):
        self._conn = http.HTTPSConnection(self._host)
        self._set_headers()
        inform('Connected to {}'.format(self._host))

    def _set_cookie(self):
        resp = self.get_response('/')
        self._headers['Cookie'] = resp.getheader('set-cookie')
        resp.read()

    def _set_headers(self):
        self._headers = HEADERS

    def _get_response(self, request):
        # inform('Requesting {}'.format(request))
        self._conn.request('GET', request, headers=self._headers)
        resp = self._conn.getresponse()
        # inform('Response is {}: {}'.format(resp.status, resp.reason))
        return resp

    @_repeat_on(NETWORK_ERRORS)
    def get_response(self, request):
        return self._get_response(request)

    @_repeat_on(TypeError)
    def get_json(self, request):
        json_data = {}

        resp = self.get_response(request)
        data = resp.read()

        try:
            data = data.decode('utf8')
        except (AttributeError, UnicodeDecodeError):
            pass

        if resp.status == 200:
            try:
                json_data = json.loads(data)
            except ValueError:
                inform('Cannot decode json')

        return resp.status, json_data

    @_repeat_on(NETWORK_ERRORS)
    def get_file(self, request):
        return self._get_response(request).read()


class Thread:

    def __init__(self, section, number):
        self._section = section
        self._last_post = 0
        self._url = THREAD_URL.format(section, number)

    def get_webms(self, fetch_tool):
        # inform('Searching for webms. Last post: {}'.format(self._last_post))
        webms = []
        status, data = fetch_tool(self._url)

        if status == 404:
            return None
        elif status != 200:
            return webms

        found_webms_count = 0

        for post in data['threads'][0]['posts']:

            if post['num'] <= self._last_post:
                continue

            try:
                files = post['files']
            except KeyError:
                continue

            for f in files:
                if f.get('type', None) == WEBM:
                    found_webms_count += 1
                    webm = RESOURCE_URL.format(self._section, f['path'])
                    thumb = RESOURCE_URL.format(self._section, f['thumbnail'])
                    md5 = f['md5']
                    webms.append((webm, thumb, md5))

        inform('Found {} webms'.format(found_webms_count))
        self._last_post = data['threads'][0]['posts'][-1]['num']
        return webms

    def __str__(self):
        return self._url


class Catalog:

    def __init__(self, section):

        self._url = CATALOG_URL.format(section)
        self._section = section
        self._threads = set()

    def get_threads(self, fetch_tool):
        alive_threads = set()
        result = []
        status, data = fetch_tool(self._url)

        if status != 200:
            return result

        try:
            threads = data['threads']
        except KeyError as e:
            inform(e, threads.keys(), self._url, level=2)
            return result

        for thread in threads:
            number = int(thread['num'])
            if number not in self._threads:
                result.append(Thread(self._section, number))
                self._threads.add(number)

            alive_threads.add(number)

        inform('Found {} threads'.format(len(result)))
        self._threads = alive_threads
        return result

    def __str__(self):
        return self._url


def except_key_interrupt(function):
    def _f(self):
        try:
            return function(self)
        except (KeyboardInterrupt, SystemExit):
            return

    return _f


class Searcher(Connection):

    def __init__(self, lock, thread_Q, file_Q):
        self._lock = lock
        self._thread_Q = thread_Q
        self._file_Q = file_Q

        super().__init__()

    @except_key_interrupt
    def work(self):
        while True:
            thread = self._thread_Q.get()
            if thread == STOP_SIGNAL:
                return

            webms = thread.get_webms(self.get_json)
            if webms is not None:
                [self._file_Q.put(w) for w in webms]
                self._thread_Q.put(thread)


class Downloader(Connection):

    def __init__(self, lock, file_Q, log_Q=None, webms=False):
        self._lock = lock
        self._file_Q = file_Q
        self._log_Q = log_Q
        self._webms = webms

        super().__init__()

    def _log(self, data):
        if not self._log_Q:
            return

        with self._lock:
            self._log_Q.put(';'.join(data))

    def _download(self, data):
        webm, thumb, md5 = data
        filename, webm_file, thumb_file = None, None, None

        if self._webms:
            webm_file = self.get_file(webm)

        thumb_file = self.get_file(thumb)
        filename = os.path.realpath(thumb).split(os.sep)[-1].split('.')[0]

        return filename, webm_file, thumb_file

    def work(self, callback=lambda *args: None):
        while True:
            data = self._file_Q.get()
            if data == STOP_SIGNAL:
                return

            filename, webm, thumb = self._download(data)
            self._log(data)

            callback(filename, webm, thumb)


class Logger:

    def __init__(self, lock, log):
        self._lock = lock
        self._log = log
        self._time = time.time()
        self._wait_time = 5 * 1
        self._file = open(datetime.now().strftime('%Y %m %d %H-%M.txt'), 'w')

    def work(self):
        while True:
            time.sleep(2)
            t = time.time()
            if t - self._time > self._wait_time:
                self._time = t
                with self._lock:
                    inform('Writing', level=ERROR)
                    lines = []
                    while not self._log.empty():
                        lines.append(self._log.get())
                    self._file.write(
                        '%s\n' % '\n'.join([str(each) for each in lines]))

    def __del__(self):
        self._file.close()


class MainWorker(Connection):

    def __init__(self, sections, searcher=Searcher, downloader=Downloader):
        self._lock = threading.RLock()
        self._thread_Q = queue.Queue()
        self._file_Q = queue.Queue()
        self._log_Q = queue.Queue()

        self._Searcher = searcher
        self._Downloader = downloader

        self.catalogs = [Catalog(section) for section in sections]

        super().__init__()

    def _start_workers(self):
        for _ in range(SEARCHERS):
            s = self._Searcher(self._lock, self._thread_Q, self._file_Q)
            t = threading.Thread(target=s.work)
            t.start()

        for _ in range(DOWNLOADERS):
            d = self._Downloader(
                self._lock, self._file_Q, self._log_Q, webms=False)
            t = threading.Thread(target=d.work)
            t.start()

        logger = Logger(self._lock, self._log_Q)
        threading.Thread(target=logger.work).start()

    def _exit(self):
        for _ in range(DOWNLOADERS + SEARCHERS):
            self._file_Q.put(STOP_SIGNAL)
            self._thread_Q.put(STOP_SIGNAL)

    def work(self):
        try:
            self._start_workers()
            while True:
                for catalog in self.catalogs:
                    [self._thread_Q.put(t)
                     for t in catalog.get_threads(self.get_json)]
                time.sleep(5)
        except (KeyboardInterrupt, SystemExit):
            self._exit()


if __name__ == '__main__':
    sections = ['vg', 'b', 'a', 'mov']
    main_worker = MainWorker(sections)
    main_worker.work()

    # def f(q, l):
    #     i = 0
    #     while True:
    #         with l:
    #             q.put(i)
    #         i += 1
    #         inform('put ', i)
    #         time.sleep(0.1)

    # def g(q, l):
    #     while True:
    #         time.sleep(2)
    #         with l:
    #             while not q.empty():
    #                 inform(q.get())
    #                 time.sleep(0.2)
    #         inform('over')

    # l = threading.RLock()
    # q = queue.Queue()

    # threading.Thread(target=f, args=[q, l]).start()
    # threading.Thread(target=g, args=[q, l]).start()
