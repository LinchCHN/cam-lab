"""十行最小示例: 打开第一台可用设备并预览(带内嵌时间戳 OSD)。"""
import cv2

import camlab
from camlab.overlay import OverlayConfig, make_timestamped

devices = camlab.list_all_devices()
print("设备:", [d.label() for d in devices])
session = camlab.registry.open(devices[0].backend, devices[0].id)
print("实际模式:", session.start().label())

while True:
    frame = session.read(timeout=1.0)
    if frame is None:
        continue
    cv2.imshow("cam-lab minimal (q退出)", make_timestamped(frame, OverlayConfig(), {}))
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break
session.close()
cv2.destroyAllWindows()
