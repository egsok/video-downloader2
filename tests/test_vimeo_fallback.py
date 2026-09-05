"""Vimeo's login-only API must not block videos accessible via the player."""

from unittest.mock import MagicMock

import pytest
from yt_dlp.utils import DownloadCancelled, DownloadError, ExtractorError

from core.downloader import Downloader, DownloaderError


URL = 'https://vimeo.com/1234567890/0123456789?fl=pl&fe=ti'
PLAYER = 'https://player.vimeo.com/video/1234567890?h=0123456789'
LOGIN_ERROR = '[vimeo] 1234567890: The web client only works when logged-in.'


@pytest.fixture
def harness(monkeypatch, tmp_path):
    monkeypatch.setattr('utils.i18n.get_current_language', lambda: 'en')
    downloader = Downloader.__new__(Downloader)
    monkeypatch.setattr(downloader, '_get_base_opts', lambda: {})
    factory = MagicMock()
    monkeypatch.setattr('core.downloader.yt_dlp.YoutubeDL', factory)
    ydl = factory.return_value.__enter__.return_value
    ydl.sanitize_info.side_effect = lambda info: info
    info = {'title': 'A video', 'duration': 60, 'extractor': 'vimeo'}
    final_path = str(tmp_path / 'A video [720p].mp4')

    def run(mode, outcomes, url=URL, cancel_check=None):
        calls = []
        pending = iter(outcomes)
        metadata = []
        progress = []

        def attempt(target, **kwargs):
            calls.append(target if mode == 'info' else target[0])
            outcome = next(pending)
            if isinstance(outcome, Exception):
                raise outcome
            if mode == 'info':
                assert kwargs == {'download': False}
                return info
            opts = factory.call_args.args[0]
            opts['progress_hooks'][0]({
                'status': 'finished', 'filename': 'intermediate.mp4', 'info_dict': info,
            })
            collector = ydl.add_post_processor.call_args.args[0]
            collector.filepath = final_path
            return 0

        ydl.extract_info.side_effect = attempt
        ydl.download.side_effect = attempt
        run.calls = calls
        run.metadata = metadata
        run.progress = progress
        if mode == 'info':
            return downloader.get_info(url)
        return downloader.download(
            url, str(tmp_path), quality='720p', cancel_check=cancel_check,
            info_callback=metadata.append,
            progress_callback=lambda *args: progress.append(args),
        )

    run.factory = factory
    run.final_path = final_path
    return run


@pytest.mark.parametrize('mode', ['info', 'download'])
def test_original_url_is_used_when_upstream_works(harness, mode):
    harness(mode, [None])
    assert harness.calls == [URL]


@pytest.mark.parametrize('mode', ['info', 'download'])
@pytest.mark.parametrize('error_type', [DownloadError, ExtractorError])
def test_login_error_retries_player_preserving_hash_and_download_behavior(harness, mode, error_type):
    result = harness(mode, [error_type(LOGIN_ERROR), None])
    assert harness.calls == [URL, PLAYER]
    if mode == 'info':
        assert result.url == URL
        assert result.title == 'A video'
    else:
        assert result == harness.final_path
        assert len(harness.metadata) == 1
        assert harness.metadata[0].url == URL
        assert harness.progress[-1] == (100, 0, 'completed')
        assert harness.factory.call_args.args[0]['format'].startswith('bestvideo[height<=720]')


@pytest.mark.parametrize('mode', ['info', 'download'])
@pytest.mark.parametrize('message', [
    '[vimeo] Connection timeout', '[vimeo] HTTP Error 401: Unauthorized',
    '[vimeo] This video is protected by a password', '[vimeo] Private video',
    '[youtube] The web client only works when logged-in.',
])
def test_other_failures_do_not_trigger_player(harness, mode, message):
    with pytest.raises(DownloaderError):
        harness(mode, [DownloadError(message)])
    assert harness.calls == [URL]


@pytest.mark.parametrize('url', [
    PLAYER, 'https://youtube.com/watch?v=1234567890',
    'https://vimeo.com.evil.example/1234567890',
    'https://vimeo.com/showcase/1234567890',
    'https://vimeo.com/1234567890/unknown-path',
])
def test_only_direct_vimeo_video_urls_are_rewritten(harness, url):
    with pytest.raises(DownloaderError):
        harness('download', [DownloadError(LOGIN_ERROR)], url=url)
    assert harness.calls == [url]


@pytest.mark.parametrize(('url', 'player'), [
    ('https://vimeo.com/123', 'https://player.vimeo.com/video/123'),
    ('https://www.vimeo.com/123/?h=0123456789', 'https://player.vimeo.com/video/123?h=0123456789'),
])
def test_public_urls_and_query_hashes(harness, url, player):
    harness('info', [DownloadError(LOGIN_ERROR), None], url=url)
    assert harness.calls == [url, player]


@pytest.mark.parametrize('mode', ['info', 'download'])
@pytest.mark.parametrize('language', ['en', 'ru'])
def test_failed_player_stops_with_localized_cookie_hint(harness, monkeypatch, mode, language):
    monkeypatch.setattr('utils.i18n.get_current_language', lambda: language)
    with pytest.raises(DownloaderError) as error:
        harness(mode, [DownloadError(LOGIN_ERROR), DownloadError(LOGIN_ERROR)])
    assert harness.calls == [URL, PLAYER]
    assert 'Vimeo' in str(error.value)
    assert 'cookies' in str(error.value)
    assert ('Settings' if language == 'en' else 'настройках') in str(error.value)
    assert 'web client' not in str(error.value)


def test_cancellation_between_attempts_prevents_retry(harness):
    checks = iter([False, True])
    with pytest.raises(DownloadCancelled):
        harness('download', [DownloadError(LOGIN_ERROR)], cancel_check=lambda: next(checks))
    assert harness.calls == [URL]


def test_cancellation_inside_player_propagates(harness):
    with pytest.raises(DownloadCancelled):
        harness('download', [DownloadError(LOGIN_ERROR), DownloadCancelled('Cancelled')])
    assert harness.calls == [URL, PLAYER]
