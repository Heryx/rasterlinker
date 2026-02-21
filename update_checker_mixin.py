# -*- coding: utf-8 -*-
"""GitHub release/tag update checker for GeoSurvey Studio."""

import configparser
import json
import os
import re
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

class UpdateCheckerMixin:
    _VERSION_RE = re.compile(r"(\d+(?:\.\d+){0,5})")
    _GITHUB_REPO_RE = re.compile(r"github\.com[:/]+([^/]+)/([^/#?]+)", re.IGNORECASE)

    def _update_settings_key(self, key):
        if hasattr(self, "_settings_key"):
            return self._settings_key(f"updates/{key}")
        return f"GeoSurveyStudio/updates/{key}"

    def _plugin_metadata_general(self):
        cached = getattr(self, "_metadata_general_cache", None)
        if isinstance(cached, dict):
            return cached

        path = os.path.join(getattr(self, "plugin_dir", ""), "metadata.txt")
        parser = configparser.ConfigParser()
        try:
            parser.read(path, encoding="utf-8")
        except Exception:
            parser.read(path)
        data = {}
        if parser.has_section("general"):
            for key, value in parser.items("general"):
                data[str(key).strip().lower()] = str(value).strip()
        self._metadata_general_cache = data
        return data

    def _local_plugin_version(self):
        return self._plugin_metadata_general().get("version", "")

    def _extract_github_owner_repo(self, value):
        txt = str(value or "").strip()
        if not txt:
            return None, None

        # Support short notation: owner/repo
        if "/" in txt and "://" not in txt and "github.com" not in txt.lower():
            parts = [p.strip() for p in txt.split("/", 1)]
            if len(parts) == 2 and parts[0] and parts[1]:
                owner, repo = parts[0], parts[1]
                if repo.lower().endswith(".git"):
                    repo = repo[:-4]
                return owner, repo

        match = self._GITHUB_REPO_RE.search(txt)
        if not match:
            return None, None
        owner = match.group(1).strip()
        repo = match.group(2).strip()
        if repo.lower().endswith(".git"):
            repo = repo[:-4]
        if owner and repo:
            return owner, repo
        return None, None

    def _github_owner_repo(self):
        general = self._plugin_metadata_general()
        # `update_repository` lets users pin update checks to a specific repo
        # without changing homepage/tracker links.
        for key in ("update_repository", "repository", "homepage", "tracker"):
            owner, repo = self._extract_github_owner_repo(general.get(key, ""))
            if owner and repo:
                return owner, repo
        return None, None

    def _normalize_version_text(self, value):
        txt = str(value or "").strip()
        if txt.lower().startswith("v"):
            txt = txt[1:].strip()
        match = self._VERSION_RE.search(txt)
        if not match:
            return ""
        return match.group(1)

    def _version_tuple(self, value):
        norm = self._normalize_version_text(value)
        if not norm:
            return tuple()
        out = []
        for chunk in norm.split("."):
            try:
                out.append(int(chunk))
            except Exception:
                out.append(0)
        return tuple(out)

    def _is_remote_version_newer(self, local_version, remote_version):
        left = self._version_tuple(local_version)
        right = self._version_tuple(remote_version)
        if not left or not right:
            return False
        max_len = max(len(left), len(right))
        left = left + (0,) * (max_len - len(left))
        right = right + (0,) * (max_len - len(right))
        return right > left

    def _github_get_json(self, url):
        req = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "GeoSurveyStudio-QGIS-Plugin",
            },
        )
        with urlopen(req, timeout=6) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    def _fetch_latest_remote_version(self):
        owner, repo = self._github_owner_repo()
        if not owner or not repo:
            raise RuntimeError("GitHub repository URL is not configured in metadata.")

        base_api = f"https://api.github.com/repos/{owner}/{repo}"
        release_url = f"{base_api}/releases/latest"
        tags_url = f"{base_api}/tags?per_page=1"
        project_url = f"https://github.com/{owner}/{repo}"

        try:
            release = self._github_get_json(release_url)
            tag_name = str(release.get("tag_name") or release.get("name") or "").strip()
            if tag_name:
                return {
                    "version": tag_name,
                    "html_url": str(release.get("html_url") or f"{project_url}/releases"),
                    "notes": str(release.get("body") or "").strip(),
                    "source": "release",
                }
        except HTTPError as e:
            # 404 = no releases; fallback to tags.
            if int(getattr(e, "code", 0) or 0) != 404:
                raise

        tags = self._github_get_json(tags_url)
        if isinstance(tags, list) and tags:
            tag = tags[0] if isinstance(tags[0], dict) else {}
            tag_name = str(tag.get("name") or "").strip()
            if tag_name:
                return {
                    "version": tag_name,
                    "html_url": f"{project_url}/tags",
                    "notes": "",
                    "source": "tag",
                }
        raise RuntimeError("No releases/tags available on GitHub.")

    def _show_update_available_dialog(self, local_version, remote_version, page_url, source):
        try:
            from qgis.PyQt.QtCore import QUrl
            from qgis.PyQt.QtGui import QDesktopServices
            from qgis.PyQt.QtWidgets import QMessageBox
        except Exception:
            try:
                from PyQt5.QtCore import QUrl
                from PyQt5.QtGui import QDesktopServices
                from PyQt5.QtWidgets import QMessageBox
            except Exception:
                self._notify_update_status(
                    f"Update available: {local_version} -> {remote_version}. {page_url}",
                    warn=False,
                )
                return
        parent = self._ui_parent() if hasattr(self, "_ui_parent") else None
        msg = QMessageBox(parent)
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle("Update Available")
        src = "release" if source == "release" else "tag"
        msg.setText(
            f"A newer {src} is available for GeoSurvey Studio.\n\n"
            f"Current version: {local_version}\n"
            f"Latest version: {remote_version}"
        )
        open_btn = msg.addButton("Open Download Page", QMessageBox.ActionRole)
        msg.addButton("Later", QMessageBox.RejectRole)
        msg.exec_()
        if msg.clickedButton() is open_btn and page_url:
            QDesktopServices.openUrl(QUrl(page_url))

    def _notify_update_status(self, text, warn=False, duration=7):
        if warn:
            try:
                self.iface.messageBar().pushWarning("GeoSurvey Studio", text)
                return
            except Exception:
                pass
        try:
            self.iface.messageBar().pushInfo("GeoSurvey Studio", text)
            return
        except Exception:
            pass
        try:
            from qgis.PyQt.QtWidgets import QMessageBox
        except Exception:
            try:
                from PyQt5.QtWidgets import QMessageBox
            except Exception:
                return
        if warn:
            QMessageBox.warning(self._ui_parent() if hasattr(self, "_ui_parent") else None, "Update Check", text)
        else:
            QMessageBox.information(self._ui_parent() if hasattr(self, "_ui_parent") else None, "Update Check", text)

    def _check_for_updates(self, manual=False):
        local_version = self._local_plugin_version()
        if not local_version:
            if manual:
                self._notify_update_status("Local plugin version not found in metadata.", warn=True)
            return

        try:
            info = self._fetch_latest_remote_version()
        except (URLError, HTTPError) as e:
            if manual:
                self._notify_update_status(f"Unable to contact GitHub: {e}", warn=True)
            return
        except Exception as e:
            if manual:
                self._notify_update_status(f"Update check failed: {e}", warn=True)
            return

        remote_version = str(info.get("version") or "").strip()
        if not remote_version:
            if manual:
                self._notify_update_status("No valid remote version found.", warn=True)
            return

        if self._is_remote_version_newer(local_version, remote_version):
            self._show_update_available_dialog(
                local_version=local_version,
                remote_version=remote_version,
                page_url=str(info.get("html_url") or ""),
                source=str(info.get("source") or ""),
            )
            return

        if manual:
            self._notify_update_status(f"You are up to date (v{local_version}).", warn=False)

    def check_for_updates_manual(self):
        self._check_for_updates(manual=True)

    def maybe_check_for_updates_on_start(self):
        # Run once per session and at most once per day.
        if getattr(self, "_update_checked_this_session", False):
            return
        self._update_checked_this_session = True

        check_on_start = self.settings.value(self._update_settings_key("check_on_start"), True, type=bool)
        if not bool(check_on_start):
            return

        today = date.today().isoformat()
        last = str(self.settings.value(self._update_settings_key("last_check_date"), "") or "").strip()
        if last == today:
            return
        self.settings.setValue(self._update_settings_key("last_check_date"), today)
        self._check_for_updates(manual=False)
