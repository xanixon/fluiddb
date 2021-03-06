"""Logic for parsing log files generated by the Fluidinfo API service."""

from datetime import datetime
from json import loads

from storm.expr import Alias
from storm.locals import Count, Desc, DateTime, TimeDelta, Int, Unicode


def loadLogs(path, store):
    """Load log data from a file and store it in a SQLite database.

    @param path: The path of the log file to parse.
    @param store: The SQLite database to store parsed results in.
    """
    parser = LogParser()
    for i, item in enumerate(parser.parse(path)):
        store.add(item)
        if i % 100 == 0:
            store.commit()
    store.commit()


def loadTraceLogs(path, store, oldFormat=None):
    """Load trace logs from a directory and store them in a SQLite database.

    @param path: The path of the log file to parse.
    @param store: The SQLite database to store parsed results in.
    @param oldFormat: Optionally indicates if the old format should be used.
    """
    parser = TraceLogParser()
    parseMethod = parser.parseOldFormat if oldFormat else parser.parse
    for i, traceLog in enumerate(parseMethod(path)):
        store.add(traceLog)
        if i % 100 == 0:
            store.commit()
    store.commit()


def reportErrorSummary(store):
    """Get a count of errors grouped by exception class and message.

    @param store: The C{Store} to fetch data from.
    @return: A list of C{(count, exception-class, message)} 3-tuples.  The
        count is automatically converted to a string.
    """
    count = Alias(Count())
    result = store.find((count, ErrorLine.exceptionClass, ErrorLine.message))
    result = result.group_by(ErrorLine.exceptionClass, ErrorLine.message)
    result = result.order_by(Desc(count), ErrorLine.exceptionClass)
    return [(str(count), exceptionClass, message)
            for count, exceptionClass, message in result]


def reportErrorTracebacks(store):
    """
    Generator yields a count of errors and their tracebacks grouped by
    exception class and message.

    @param store: The C{Store} to fetch data from.
    @return: A sequence of C{(count, exception-class, message, traceback)}
        4-tuples.
    """
    count = Alias(Count())
    result = store.find((count, ErrorLine.exceptionClass,
                         ErrorLine.message, ErrorLine.traceback))
    result = result.group_by(ErrorLine.exceptionClass, ErrorLine.message)
    return result.order_by(Desc(count), ErrorLine.exceptionClass)


def reportTraceLogSummary(store, limit, endpoint=None):
    """Generator yields the slowest requests from the last 50000 requests.

    @param store: The C{Store} to fetch data from.
    @return: A sequence of C{(duration, endpoint, filename)} 3-tuples.
    """
    result = store.find(TraceLog)
    result = result.order_by(Desc(TraceLog.duration))
    result = result.config(limit=50000)
    subselect = result.get_select_expr(TraceLog.id)

    result = store.find(TraceLog, TraceLog.id.is_in(subselect))
    result = result.order_by(Desc(TraceLog.duration))
    result = result.config(limit=limit)
    result = result.values(TraceLog.duration, TraceLog.endpoint,
                           TraceLog.sessionID)
    for duration, endpoint, sessionID in result:
        yield str(duration), endpoint, sessionID


class LogParser(object):
    """Parser reads log files generated by the Fluidinfo API service."""

    def parse(self, path):
        """
        Generator parses a log file and yields L{StatusLine} and L{ErrorLine}
        instances built from data in the log.

        @param path: The path of the log file to parse.
        """
        with open(path, 'r') as stream:
            reader = FileReader(stream)
            for line in reader:
                if 'INFO' in line and '127.0.0.1' in line:
                    yield self._parseStatusLine(line)
                elif 'ERROR' in line:
                    yield self._parseErrorLine(line, reader)

    def _parseTime(self, parts):
        """Parse the time from the log line.

        @param parts: The log line, split into parts, to parse.
        @return: A C{datetime} with the time from the log line.
        """
        time = '%s %s' % (parts[0], parts[1])
        time, microseconds = time.split(',')
        time = datetime.strptime(time, '%Y-%m-%d %H:%M:%S')
        return datetime(time.year, time.month, time.day, time.hour,
                        time.minute, time.second, int(microseconds))

    def _parseStatusLine(self, line):
        """
        Parse a status line from the log and convert it into a L{StatusLine}.

        @param line: The log line to parse.
        @return: A L{StatusLine} instance representing the log line.
        """
        parts = line.split()
        time = self._parseTime(parts)
        code = int(parts[11])
        method = parts[8][1:]
        endpoint = parts[9]
        agent = parts[14][1:-1]
        contentLength = parts[12]
        contentLength = 0 if contentLength == '-' else int(contentLength)
        return StatusLine(time, code, method, endpoint, contentLength, agent)

    def _parseErrorLine(self, line, reader):
        """Parse an error line from the log and convert it into a L{ErrorLine}.

        @param line: The log line to parse.
        @param stream: The file stream being read from.
        @return: An L{ErrorLine} instance representing the log line.
        """
        parts = line.split(' ', 2)
        time = self._parseTime(parts)
        message = parts[2]
        message = message.replace('ERROR', '').strip()
        exceptionClass = None
        traceback = None
        for line in reader:
            if line.startswith('Traceback'):
                traceback = [line]
                for errorLine in reader:
                    traceback.append(errorLine)
                    if (not errorLine.startswith(' ') and
                            not errorLine.startswith('---')):
                        exceptionClass = errorLine.split(':')[0]
                        if exceptionClass == 'None':
                            exceptionClass = None
                        break
            else:
                reader.putBack(line)
                break
        if traceback:
            traceback = ''.join(traceback)
        return ErrorLine(time, message, exceptionClass=exceptionClass,
                         traceback=traceback)


class StatusLine(object):
    """A representation of a status line from a log file."""

    __storm_table__ = 'status_lines'

    id = Int(primary=True)
    time = DateTime(allow_none=False)
    code = Int(allow_none=False)
    method = Unicode(allow_none=False)
    endpoint = Unicode(allow_none=False)
    contentLength = Int('content_length', allow_none=False)
    agent = Unicode()

    def __init__(self, time, code, method, endpoint, contentLength, agent):
        self.time = time
        self.code = code
        self.method = unicode(method)
        self.endpoint = unicode(endpoint)
        self.contentLength = contentLength
        self.agent = unicode(agent)


class ErrorLine(object):
    """A representation of an error line from a log file."""

    __storm_table__ = 'error_lines'

    id = Int(primary=True)
    time = DateTime(allow_none=False)
    message = Unicode(allow_none=False)
    exceptionClass = Unicode('exception_class')
    traceback = Unicode()

    def __init__(self, time, message, exceptionClass=None, traceback=None):
        self.time = time
        self.message = unicode(message)
        self.exceptionClass = (
            None if exceptionClass is None else unicode(exceptionClass))
        self.traceback = None if traceback is None else unicode(traceback)


class FileReader(object):
    """A simple line reader that can store lines that have been put back.

    @param stream: A C{file}-like object to read log lines from.
    """

    def __init__(self, stream):
        self._stream = stream
        self._backlog = []

    def __iter__(self):
        """Iterate over lines in the underlying stream."""
        for line in self._stream:
            while self._backlog:
                yield self._backlog.pop()
            yield line

    def putBack(self, line):
        """Queue a line for reading on the next iteration.

        @param line: The line to put back in the reader.
        """
        self._backlog.append(line)


class TraceLogParser(object):
    """Parser reads trace log files generated by the Fluidinfo API service."""

    separator = '// SEPARATOR'

    def parseOldFormat(self, path):
        """
        Generator that loads L{TraceLog} instances from an old log file. Old
        logs used multiple lines for each L{TraceLog}. This method is kept for
        backwards compatibility.

        @param path: The path to a trace log file.
        """
        with open(path, 'r') as stream:
            lines = []
            for line in stream:
                if line.startswith(self.separator):
                    yield self._parse(''.join(lines))
                    lines = []
                else:
                    lines.append(line)

    def parse(self, path):
        """Generator that loads L{TraceLog} instances from a log file.

        @param path: The path to a trace log file.
        """
        with open(path, 'r') as stream:
            for line in stream:
                yield self._parse(line)

    def _parse(self, data):
        """Parse a single trace log file.

        @param data: JSON data representing a L{Session}.
        @return: A L{TraceLog} instance.
        """
        trace = loads(data)
        sessionID = trace['id']
        startDate = datetime.strptime(trace['startDate'],
                                      '%Y-%m-%d %H:%M:%S.%f')
        stopDate = datetime.strptime(trace['stopDate'],
                                     '%Y-%m-%d %H:%M:%S.%f')
        duration = stopDate - startDate
        endpoint = trace['http']['path']
        endpoint = '/' + endpoint.split('/', 2)[1]
        return TraceLog(duration, endpoint, sessionID)


class TraceLog(object):
    """A representation of a trace log file."""

    __storm_table__ = 'trace_logs'

    id = Int(primary=True)
    duration = TimeDelta(allow_none=False)
    endpoint = Unicode(allow_none=False)
    sessionID = Unicode('session_id', allow_none=False)

    def __init__(self, duration, endpoint, sessionID):
        self.duration = duration
        self.endpoint = endpoint
        self.sessionID = sessionID
