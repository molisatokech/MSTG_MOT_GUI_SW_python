# custom_viewbox.py
from pyqtgraph import ViewBox


# class ZoomableViewBox(ViewBox):
#     def wheelEvent(self, ev):
#         if ev.angleDelta().y() != 0:
#             zoom_factor = 1.25 if ev.angleDelta().y() > 0 else 1 / 1.25
#             center = self.mapToView(ev.pos())
#             self.scaleBy((zoom_factor, zoom_factor), center)
#             ev.accept()
#         else:
#             super().wheelEvent(ev)
class ZoomableViewBox(ViewBox):
    def wheelEvent(self, ev):
        # QGraphicsSceneWheelEvent는 angleDelta가 없음, delta() 사용
        delta = ev.delta()  # int 값
        if delta != 0:
            zoom_factor = 1.25 if delta > 0 else 1 / 1.25
            center = self.mapToView(ev.pos())
            self.scaleBy((zoom_factor, zoom_factor), center)
            ev.accept()
        else:
            super().wheelEvent(ev)
