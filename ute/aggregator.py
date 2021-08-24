import struct
from pickle import loads
from logging import basicConfig, makeLogRecord, getLogger
from logging.handlers import DEFAULT_TCP_LOGGING_PORT
from socketserver import ThreadingTCPServer, StreamRequestHandler


class LogRecordStreamHandler(StreamRequestHandler):
    """Handler for a streaming logging request.

    This basically logs the record using whatever logging policy is
    configured locally.
    """

    def handle(self) -> None:
        """
        Handle multiple requests - each expected to be a 4-byte length,
        followed by the LogRecord in pickle format. Logs the record
        according to whatever policy is configured locally.
        """
        while True:
            chunk = self.connection.recv(4)
            if len(chunk) < 4:
                break
            slen = struct.unpack('>L', chunk)[0]
            chunk = self.connection.recv(slen)
            while len(chunk) < slen:
                chunk = chunk + self.connection.recv(slen - len(chunk))
            obj = self.unPickle(chunk)
            record = makeLogRecord(obj)
            self.handleLogRecord(record)

    def unPickle(self, data: bytes):
        return loads(data)

    def handleLogRecord(self, record) -> None:
        # if a name is specified, we use the named logger rather than the one
        # implied by the record.
        if self.server.logname is not None:
            name = self.server.logname
        else:
            name = record.name
        logger = getLogger(name)
        # N.B. EVERY record gets logged. This is because Logger.handle
        # is normally called AFTER logger-level filtering. If you want
        # to do filtering, do it at the client end to save wasting
        # cycles and network bandwidth!
        logger.handle(record)


class LogRecordSocketReceiver(ThreadingTCPServer):
    """
    Simple TCP socket-based logging receiver suitable for testing.
    """

    allow_reuse_address = True

    def __init__(self, host: str = '0.0.0.0',
                 port: int = DEFAULT_TCP_LOGGING_PORT,
                 handler = LogRecordStreamHandler) -> None:
        ThreadingTCPServer.__init__(self, (host, port), handler)
        self.abort = 0
        self.timeout = 1
        self.logname = 'ute'

    def serve_until_stopped(self):
        import select
        abort = 0
        while not abort:
            rd, wr, ex = select.select([self.socket.fileno()],
                                       [], [],
                                       self.timeout)
            if rd:
                self.handle_request()
            abort = self.abort

def main() -> None:
    basicConfig(format = '%(name)-12s %(levelname)-7s %(message)s')
    tcpserver = LogRecordSocketReceiver()
    print('Starting TCP server...')
    tcpserver.serve_until_stopped()

if __name__ == '__main__':
    main()
