import json
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

import psutil

from startup.configuration import (
    read_agent_settings,
    validate_agent_settings,
    write_agent_settings,
    write_watch_directories,
)
from startup.chickensee_desktop import (
    ChickenSeeDesktop,
    format_queue_progress,
    request_stop,
    start_supervisor,
    stop_processes_after_signal,
)


class TestDesktopConfiguration(unittest.TestCase):
    def test_queue_progress_does_not_invent_percentages(self):
        self.assertEqual(format_queue_progress("QUEUED", None), "Waiting")
        self.assertEqual(format_queue_progress("WAITING", None), "Waiting")
        self.assertEqual(format_queue_progress("PROCESSING", None), "—")
        self.assertEqual(format_queue_progress("PROCESSING", 37.5), "37.5%")
        self.assertEqual(format_queue_progress("PROCESSING", 150), "100.0%")

    def test_offline_queue_does_not_overwrite_synced_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage = Path(temporary)
            results_dir = storage / "results"
            results_dir.mkdir()
            status_file = results_dir / "queue_status.json"
            original = '{"items":[{"video_filename":"completed.mp4","status":"COMPLETED"}]}'
            status_file.write_text(original, encoding="utf-8")

            desktop = object.__new__(ChickenSeeDesktop)
            desktop._save_queue_status_for_sync(None)

            self.assertEqual(status_file.read_text(encoding="utf-8"), original)

    def test_new_install_defaults_to_local_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = read_agent_settings(Path(temporary))
        self.assertFalse(settings["cloud_sync"])
        self.assertEqual(settings["directories"], [])

    def test_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            watched = root / "recordings"
            storage = root / "local-results"
            watched.mkdir()
            settings = {
                "directories": [str(watched)],
                "storage_root": str(storage),
                "cloud_sync": True,
                "sync_url": "https://example.vercel.app/api/sync",
                "sync_secret": "secret-value",
                "env_values": {"MAX_RETRIES": "4"},
            }

            write_agent_settings(settings, root)
            loaded = read_agent_settings(root)

            self.assertEqual(loaded["directories"], [str(watched.resolve())])
            self.assertEqual(Path(loaded["storage_root"]), storage.resolve())
            self.assertTrue(loaded["cloud_sync"])
            self.assertEqual(loaded["sync_url"], settings["sync_url"])
            self.assertEqual(loaded["sync_secret"], settings["sync_secret"])
            self.assertEqual(loaded["env_values"]["MAX_RETRIES"], "4")
            self.assertEqual(loaded["env_values"]["SYNC_VIDEOS_TO_CLOUD"], "0")
            self.assertEqual(
                json.loads((root / "agent" / "directories.json").read_text(encoding="utf-8")),
                {"directories": [str(watched.resolve())]},
            )

    def test_removing_watch_directory_saves_only_directory_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            storage = root / "results"
            first.mkdir()
            second.mkdir()
            write_agent_settings(
                {
                    "directories": [str(first), str(second)],
                    "storage_root": str(storage),
                    "cloud_sync": False,
                    "sync_url": "",
                    "sync_secret": "",
                    "env_values": {"MAX_RETRIES": "4"},
                },
                root,
            )

            write_watch_directories([str(first)], root)
            settings = read_agent_settings(root)

            self.assertEqual(settings["directories"], [str(first.resolve())])
            self.assertEqual(settings["env_values"]["MAX_RETRIES"], "4")
            self.assertEqual(
                settings["env_values"]["WATCH_DIR"],
                Path(first).as_posix(),
            )

    def test_add_directory_persists_watch_folder_immediately(self):
        class DirectoryTree:
            def __init__(self, directories):
                self.rows = {str(index): (directory,) for index, directory in enumerate(directories)}

            def get_children(self):
                return tuple(self.rows)

            def item(self, row, _field):
                return self.rows[row]

            def insert(self, _parent, _position, values):
                row = str(len(self.rows))
                self.rows[row] = values
                return row

            def delete(self, row):
                del self.rows[row]

        with tempfile.TemporaryDirectory() as temporary:
            existing = Path(temporary) / "existing"
            added = Path(temporary) / "added"
            existing.mkdir()
            added.mkdir()
            desktop = object.__new__(ChickenSeeDesktop)
            desktop.directory_tree = DirectoryTree([str(existing)])
            desktop.test_directory_combo = {}
            desktop.test_directory = Mock()
            desktop.test_directory.get.return_value = str(existing)
            desktop.test_directory.set = Mock()
            desktop.settings = {"directories": [str(existing)]}
            desktop._log = Mock()

            with patch(
                "startup.chickensee_desktop.filedialog.askdirectory",
                return_value=str(added),
            ), patch(
                "startup.chickensee_desktop.write_watch_directories"
            ) as save_directories, patch(
                "startup.chickensee_desktop.messagebox.showinfo"
            ):
                desktop.add_directory()

            expected = [str(existing), str(added.resolve())]
            save_directories.assert_called_once_with(expected)
            self.assertEqual(desktop.settings["directories"], expected)
            self.assertEqual(
                [desktop.directory_tree.item(row, "values")[0]
                 for row in desktop.directory_tree.get_children()],
                expected,
            )

    def test_cloud_sync_requires_valid_url_and_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            watched = Path(temporary) / "recordings"
            watched.mkdir()
            settings = {
                "directories": [str(watched)],
                "storage_root": str(Path(temporary) / "store"),
                "cloud_sync": True,
                "sync_url": "http://insecure.example/sync",
                "sync_secret": "",
            }
            self.assertIn("HTTPS", validate_agent_settings(settings))

    def test_stop_signal_targets_supervisor_storage_and_agent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent_dir = root / "agent"
            configured_storage = root / "new-storage"
            supervisor_storage = root / "supervisor-storage"
            agent_dir.mkdir()
            (agent_dir / ".supervisor-state.json").write_text(
                json.dumps(
                    {
                        "process_id": 77,
                        "storage_root": str(supervisor_storage),
                    }
                ),
                encoding="utf-8",
            )

            request_stop(configured_storage, [77], agent_dir)

            self.assertTrue((agent_dir / ".stop-request").is_file())
            self.assertTrue((configured_storage / "logs" / "stop-request").is_file())
            self.assertTrue((supervisor_storage / "logs" / "stop-request").is_file())

    def test_stop_fallback_terminates_only_captured_process(self):
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )
        try:
            process = psutil.Process(child.pid)
            targets = {
                "sync-agent": [(process, process.create_time())],
                "analytics-api": [],
                "analytics-worker": [],
                "supervisor": [],
            }
            self.assertTrue(stop_processes_after_signal(targets, graceful_timeout_s=0))
            child.wait(timeout=3)
            self.assertIsNotNone(child.returncode)
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=3)

    def test_start_supervisor_launches_hidden_and_logs_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "startup" / "run-chickensee.ps1"
            script.parent.mkdir()
            script.touch()
            storage = root / "local-storage"
            with patch("startup.chickensee_desktop.subprocess.Popen") as popen:
                start_supervisor(root, storage)

            args, kwargs = popen.call_args
            self.assertEqual(args[0][0], "powershell.exe")
            self.assertIn(str(script), args[0])
            self.assertEqual(kwargs["cwd"], root)
            self.assertEqual(kwargs["stderr"], subprocess.STDOUT)
            self.assertTrue((storage / "logs" / "startup-launch.log").is_file())


    def test_device_transfer_recovers_from_stale_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent_dir = root / "agent"
            agent_dir.mkdir()

            # Simulate config transferred from another machine with DELL paths
            stale_dirs_file = agent_dir / "directories.json"
            stale_dirs_file.write_text(
                json.dumps(
                    {
                        "directories": [
                            "C:\\Users\\DELL\\Desktop\\ChickenSee_Videos(sql)\\agent\\data",
                            "C:\\Users\\DELL\\Desktop\\New folder\\ChickenSee\\recordings",
                        ]
                    }
                ),
                encoding="utf-8",
            )

            settings = read_agent_settings(root)
            # The agent/data path should auto-remap to the local project's agent/data
            expected_local_data = str((agent_dir / "data").resolve())
            self.assertIn(expected_local_data, settings["directories"])
            self.assertNotIn("C:\\Users\\DELL\\Desktop\\New folder\\ChickenSee\\recordings", settings["directories"])


if __name__ == "__main__":
    unittest.main()
