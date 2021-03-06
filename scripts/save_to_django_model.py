import os
import sys


BASE_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.append(os.path.join(BASE_DIR, 'WillBeams'))
sys.path.append(BASE_DIR)


import django
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.db.utils import IntegrityError


os.environ["DJANGO_SETTINGS_MODULE"] = 'WillBeams.settings'
django.setup()

from webm.models import Webm, get_media_folder
import scraper


class DownloadToModel(scraper.Downloader):

    def __init__(self, *args, **kwargs):
        self._webm_obj = None
        super().__init__(*args, **kwargs)

    def _download(self, data):
        md5 = data[-1]
        try:
            Webm.increase_rating(md5)
            result = None, None, None
        except Webm.DoesNotExist:
            self._webm_obj = Webm(md5=md5)
            result = super()._download(data)

        return result

    def save(self, filename, webm, thumb):
        if not filename:
            return

        if webm:
            filename += '.webm'
            path = os.path.join(
                get_media_folder(self._webm_obj, filename), filename)
            webm_path = default_storage.save(path, ContentFile(webm))
            self._webm_obj.video = webm_path

        if thumb:
            filename += '.jpg'
            path = os.path.join(
                get_media_folder(self._webm_obj, filename), filename)
            thumb_path = default_storage.save(path, ContentFile(thumb))
            self._webm_obj.thumbnail = thumb_path

        try:
            self._webm_obj.save()
        except IntegrityError:
            Webm.increase_rating(self._webm_obj.md5)

    def work(self, *args, **kwargs):
        return super().work(self.save)


if __name__ == '__main__':
    sections = ['vg', 'b', 'a', 'mov']
    worker = scraper.MainWorker(sections, downloader=DownloadToModel)
    worker.work()
