"""Tests for the thread-safe send queue (Step 4 of architecture refactor).

Verifies queue semantics: FIFO, thread safety, empty-queue behavior,
and no data loss under multiple puts.
"""

import queue
import threading


def _make_queue():
    """Create a queue.Queue matching the one in main.py."""
    return queue.Queue()


class TestSendQueueAcceptsItems:
    """Queue should accept (mode, customer_name, reply_text) tuples."""

    def test_put_and_get_single(self):
        q = _make_queue()
        q.put(("auto", "Alice", "Hello"))
        mode, name, text = q.get_nowait()
        assert mode == "auto"
        assert name == "Alice"
        assert text == "Hello"

    def test_put_manual_mode(self):
        q = _make_queue()
        q.put(("manual", "Bob", "Manual reply"))
        mode, name, text = q.get_nowait()
        assert mode == "manual"
        assert name == "Bob"
        assert text == "Manual reply"


class TestSendQueueFIFO:
    """Items must come out in the same order they went in."""

    def test_fifo_order(self):
        q = _make_queue()
        q.put(("auto", "A", "first"))
        q.put(("auto", "B", "second"))
        q.put(("auto", "C", "third"))

        _, name1, _ = q.get_nowait()
        _, name2, _ = q.get_nowait()
        _, name3, _ = q.get_nowait()

        assert name1 == "A"
        assert name2 == "B"
        assert name3 == "C"


class TestSendQueueEmpty:
    """get_nowait on empty queue must raise queue.Empty."""

    def test_empty_raises(self):
        q = _make_queue()
        try:
            q.get_nowait()
            assert False, "Expected queue.Empty to be raised"
        except queue.Empty:
            pass  # expected


class TestSendQueueNoDataLoss:
    """Multiple puts must not lose data."""

    def test_many_puts(self):
        q = _make_queue()
        n = 100
        for i in range(n):
            q.put(("auto", f"customer_{i}", f"reply_{i}"))

        results = []
        while True:
            try:
                results.append(q.get_nowait())
            except queue.Empty:
                break

        assert len(results) == n
        # Verify ordering and content
        for i, (mode, name, text) in enumerate(results):
            assert mode == "auto"
            assert name == f"customer_{i}"
            assert text == f"reply_{i}"

    def test_mixed_modes(self):
        q = _make_queue()
        q.put(("auto", "A", "auto_msg"))
        q.put(("manual", "B", "manual_msg"))
        q.put(("auto", "C", "auto_msg2"))

        results = []
        while True:
            try:
                results.append(q.get_nowait())
            except queue.Empty:
                break

        assert len(results) == 3
        assert results[0][0] == "auto"
        assert results[1][0] == "manual"
        assert results[2][0] == "auto"


class TestSendQueueThreadSafety:
    """Concurrent puts from multiple threads must not lose data."""

    def test_concurrent_producers(self):
        q = _make_queue()
        n_per_thread = 50
        n_threads = 4
        barrier = threading.Barrier(n_threads)

        def producer(thread_id):
            barrier.wait()
            for i in range(n_per_thread):
                q.put(("auto", f"t{thread_id}_c{i}", f"reply_{i}"))

        threads = [threading.Thread(target=producer, args=(tid,))
                   for tid in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        results = []
        while True:
            try:
                results.append(q.get_nowait())
            except queue.Empty:
                break

        assert len(results) == n_per_thread * n_threads


class TestProcessSendQueue:
    """Test the _process_send_queue drain logic in isolation."""

    def test_drain_all_items(self):
        """Process function should drain all items until queue is empty."""
        q = _make_queue()
        processed = []

        def mock_do_send(customer_name, reply_text):
            processed.append((customer_name, reply_text))
            return True

        q.put(("auto", "A", "msg1"))
        q.put(("manual", "B", "msg2"))
        q.put(("auto", "C", "msg3"))

        # Simulate _process_send_queue logic
        try:
            while True:
                mode, customer_name, reply_text = q.get_nowait()
                mock_do_send(customer_name, reply_text)
        except queue.Empty:
            pass

        assert len(processed) == 3
        assert processed[0] == ("A", "msg1")
        assert processed[1] == ("B", "msg2")
        assert processed[2] == ("C", "msg3")
        assert q.empty()

    def test_drain_empty_queue_is_noop(self):
        """Processing an empty queue should do nothing, not crash."""
        q = _make_queue()
        processed = []

        def mock_do_send(customer_name, reply_text):
            processed.append((customer_name, reply_text))
            return True

        try:
            while True:
                mode, customer_name, reply_text = q.get_nowait()
                mock_do_send(customer_name, reply_text)
        except queue.Empty:
            pass

        assert len(processed) == 0
