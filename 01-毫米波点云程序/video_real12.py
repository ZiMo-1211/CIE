import sys
import cv2
import os, shutil
import time, math
import threading
import queue
import numpy as np
import serial, datetime
import re
import matplotlib.pyplot as plt
from matplotlib.animation import FFMpegWriter
import matplotlib.animation as animation
from collections import deque
from PyQt5 import QtWidgets, QtCore, QtGui
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from radar_utils import cluster_filter
from sklearn.cluster import DBSCAN
from pyqtgraph.opengl import MeshData, GLMeshItem
from pyqtgraph.opengl import GLScatterPlotItem
# 毫米波数据采集线程参数
radar_raw_data_save_path = 'received_data.txt'
radar_points_data_save_path = 'pointcloud_output.txt'
radar_points_data_save_dir = 'mmwave_ori'
radar_points_dbscan_txt_save_dir = 'mmwave_db_txt'
radar_points_dbscan_bin_save_dir = 'mmwave_db_bin'
# 视频数据采集线程参数
camera_images_save_dir = 'img_ori'
# 同步数据线程参数
max_time_diff = 100  # 时间戳容差（单位：毫秒）
align_data_save_dir = 'alignment_ori'
frame_queue = queue.Queue(maxsize=100)  # 限制最大帧缓冲数
radar_queue = queue.Queue(maxsize=100)
sync_frame_queue = queue.Queue(maxsize=100)
sync_radar_queue =queue.Queue(maxsize=100)
# 数据可视化线程参数
save_video_event = threading.Event()
win_frame_num = 3  # 窗口帧数
frame_window = deque(maxlen=win_frame_num) # 初始化滑动窗口缓存
radar_window = deque(maxlen=win_frame_num)
# 解析毫米波数据的过滤阈值参数
r_action_min = 0.05  # 最小有效距离
R_lim = 6  # 最大有效距离
X_lim = 3.0  # X轴限制
Z_lim = 3.0  # Z轴限制
Y_lim = R_lim  # Y轴限制
is_enable_data_process = False # 启动毫米波点云预处理
is_enable_video_saved = False # 启动视频保存
is_enable_vis_PG= True  # 启动pyqtgraph可视化

def simulate_lighting(color, z_value, z_min, z_max):
    brightness = 0.6 + 0.4 * ((z_value - z_min) / (z_max - z_min))  # 从0.6到1.0
    return tuple(np.clip(np.array(color[:3]) * brightness, 0, 1).tolist() + [color[3]])

def create_or_clear_directory(path):
    if os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path)

def clear_file(path):
    if os.path.exists(path):
        os.remove(path)

# 毫米波数据采集线程
class RadarCapture(threading.Thread):
    def __init__(self, radar_raw_data_save_path, radar_points_data_save_path, radar_points_data_save_dir):
        super().__init__()
        self.running = True
        self.buffer = b""  # 初始化雷达原始数据缓冲区
        self.radar_raw_data_save_path = radar_raw_data_save_path
        self.radar_points_data_save_path = radar_points_data_save_path
        self.radar_points_data_save_dir = radar_points_data_save_dir
        clear_file(self.radar_raw_data_save_path)
        clear_file(self.radar_points_data_save_path)
        create_or_clear_directory(self.radar_points_data_save_dir)
        self._buffer_max_bytes = 10 * 1024 * 1024
        self.com_port = self.openRadar()
    def openRadar(self):
        """打开雷达通信并同步数据"""
        com_port = self.openSerial()  # 打开串口
        # 等待读取到第一次合法头再返回
        start_time = time.time()
        while True:
            n = com_port.in_waiting
            if n:
                # 仅读取现有字节，不保证以换行分割
                temp = com_port.read(n)
                self.buffer += temp

                # 防止 buffer 过大
                if len(self.buffer) > self._buffer_max_bytes:
                    print("⚠️ 雷达接收缓冲过大，自动清空 self.buffer")
                    self.buffer = b""

                # 尝试找到包头
                headInd = self.buffer.find(b"\xdc\xff\xee\xff")
                if headInd >= 0:
                    # 截断到包头开始，返回 com_port（后面 get_serial_data 使用 buffer）
                    self.buffer = self.buffer[headInd:]
                    return com_port

            # 超时保护（避免无限阻塞）
            if time.time() - start_time > 10:
                # 已等待 10 s 仍未找到头，返回串口仍可操作
                return com_port
            time.sleep(0.005)

    def openSerial(self,baudRate=2000000):
        """打开串口通信"""
        serialPort = "COM7"  # 获取选择的COM端口
        # 尝试打开串口
        com_port = serial.Serial(
            port=serialPort,
            baudrate=baudRate,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=20,
        )
        com_port.close()  # 关闭后重新打开以确保状态正确
        if not com_port.is_open:
            com_port.open()

        assert com_port.is_open, "open is error"
        return com_port

    def big_to_little_endian(self, hex_str):
        if len(hex_str) % 2 != 0:
            hex_str = '0' + hex_str
        little_endian_str = ''.join(reversed([hex_str[i:i + 2] for i in range(0, len(hex_str), 2)]))
        return little_endian_str

    def hex_to_bit(self, hex_str):
        bin_str = ''.join(f'{int(c, 16):04b}' for c in hex_str)
        return bin_str

    def bin_to_uint32(self, bin_str):
        if len(bin_str) > 32:
            raise ValueError("二进制字符串不能超过32位")
        num = int(bin_str, 2)
        num_uint32 = num & 0xFFFFFFFF
        return num_uint32

    def bin_to_uint16(self, bin_str):
        if len(bin_str) > 16:
            raise ValueError("二进制字符串不能超过16位")
        num = int(bin_str, 2)
        num_uint16 = num & 0xFFFF
        return num_uint16

    def bin_to_int16(self, bin_str):
        num = int(bin_str, 2)
        if num >= 2 ** 15:
            num -= 2 ** 16
        # 融入 bits_to_int 的逻辑：减去 16384
        return num - 16384

    def bin_to_int(self, bin_str):
        if bin_str[0] == '1':
            original = -((int(bin_str, 2) ^ (2 ** len(bin_str) - 1)) + 1)
        else:
            original = int(bin_str, 2)
        # 融入 bits_to_int 的逻辑：减去 16384
        return original - 16384

    def check_point_number(self, point_number):
        for i in range(0, len(point_number)):
            if point_number[i] != i:
                return False
        return True

    def extract_head(self, hex_str, head_length=40, pattern=r'DCFFEEFF(.*?)D3FFEEFF'):
        matches = re.findall(pattern, hex_str)
        if len(matches) != 1 or len(matches[0]) != head_length:
            return None
        else:
            return matches[0]

    def extract_bpoint(self, hex_str, gt_points_num, pattern=r'CBFEDDFF(.*?)C4FEDDFF'):
        cloud = re.findall(pattern, hex_str)
        if len(cloud) != 1:
            return None
        matches = self.split_by_length(cloud[0])
        point_number = []
        for match in matches:
            body_hex = self.big_to_little_endian(match)
            point_number.append(self.bin_to_uint16(self.hex_to_bit(body_hex)[-10:]))
        if not self.check_point_number(point_number):
            print("点云序号不匹配, " + str(point_number))
            print(hex_str)
            return None
        else:
            # print("GT点云数量：%s，实际点云数量：%s" % (gt_points_num, len(matches)))
            return matches

    def getrad(self, angle):
        """Convert angle from degrees to radians."""
        return math.radians(angle)

    def sph2cart(self, r, a, v):
        arad = self.getrad(a)
        vrad = self.getrad(v)
        x = r * math.cos(vrad) * math.sin(arad)
        y = r * math.cos(vrad) * math.cos(arad)
        z = r * math.sin(vrad)
        return [x, y, z]

    def split_by_length(self, text, length=40):
        parts = []
        for i in range(0, len(text), length):
            if (len(text[i:i + length]) == length):
                parts.append(text[i:i + length])
        return parts

    def jiexi_head(self, match):
        # print("小端序")
        head_hex = self.big_to_little_endian(match)
        assert self.bin_to_uint32(self.hex_to_bit(head_hex)[-74:-64]) == self.bin_to_uint32(
            self.hex_to_bit(head_hex)[-94:-84]), "BK点数和raw_number不匹配"
        return self.bin_to_uint32(self.hex_to_bit(head_hex)[-74:-64])  # 返回一个点云的点总数

    def jiexi_bpoint(self, match, timestamp_str):
        body_hex = self.big_to_little_endian(match)
        point_number = self.bin_to_uint16(self.hex_to_bit(body_hex)[-10:])
        snr = self.bin_to_uint16(self.hex_to_bit(body_hex)[-25:-10])
        distance = self.bin_to_uint32(self.hex_to_bit(body_hex)[-64:-32])
        speed = self.bin_to_int16(self.hex_to_bit(body_hex)[-79:-64]) / 100
        azimuth = self.bin_to_int16(self.hex_to_bit(body_hex)[-94:-79])
        elevation = self.bin_to_int16(self.hex_to_bit(body_hex)[-111:-96])
        distance, azimuth, elevation = distance / 100, azimuth / 100, elevation / 100
        x, y, z = self.sph2cart(distance, azimuth, elevation)

        # 过滤无效点
        if distance <= r_action_min:
            return
        if np.abs(x) > X_lim or np.abs(y) > Y_lim or np.abs(z) > Z_lim:
            return

        # 只保留解析结果，不写入文件
        return {'x': x, 'y': y, 'z': z, 'v': speed, 'SNR': snr}

    # def jiexi_bpoint(self, match, timestamp_str):
    #     body_hex = self.big_to_little_endian(match)
    #     point_number = self.bin_to_uint16(self.hex_to_bit(body_hex)[-10:])
    #     snr = self.bin_to_uint16(self.hex_to_bit(body_hex)[-25:-10])
    #     distance = self.bin_to_uint32(self.hex_to_bit(body_hex)[-64:-32])
    #     speed = self.bin_to_int16(self.hex_to_bit(body_hex)[-79:-64]) / 100
    #     azimuth = self.bin_to_int16(self.hex_to_bit(body_hex)[-94:-79])
    #     elevation = self.bin_to_int16(self.hex_to_bit(body_hex)[-111:-96])
    #     distance, azimuth, elevation = distance / 100, azimuth / 100, elevation / 100
    #     x, y, z = self.sph2cart(distance, azimuth, elevation)
    #     # 过滤无效点
    #     if distance <= r_action_min:
    #         print(f"{timestamp_str}ms 点距离{distance}小于或等于限制距离{r_action_min}，已过滤")
    #         return
    #     if np.abs(x) > X_lim or np.abs(y) > Y_lim or np.abs(z) > Z_lim:
    #         print(f"{timestamp_str}ms 点坐标（{x}，{y}，{z}）大于限制坐标"
    #               f"（{X_lim}，{Y_lim}，{Z_lim}），已过滤")
    #         return
    #     output_line = (
    #         f"{timestamp_str}ms    P {point_number}    "
    #         f"X={str(x)[:str(x).find('.') + 4]}m    "
    #         f"Y={str(y)[:str(y).find('.') + 4]}m    "
    #         f"Z={str(z)[:str(z).find('.') + 4]}m    "
    #         f"V={str(speed)[:str(speed).find('.') + 3]}m/s    "
    #         f"SNR={snr:.2f}    "
    #     )
    #     with open(self.radar_points_data_save_path, 'a') as file:
    #         file.write(output_line + "\n")
    #
    #     output_path = os.path.join(self.radar_points_data_save_dir, f'frame_{timestamp_str}ms.txt')
    #     with open(output_path, 'a') as f:
    #         points_str = (
    #             f"P {point_number}    "
    #             f"X={str(x)[:str(x).find('.') + 4]}m    "
    #             f"Y={str(y)[:str(y).find('.') + 4]}m    "
    #             f"Z={str(z)[:str(z).find('.') + 4]}m    "
    #             f"V={str(speed)[:str(speed).find('.') + 3]}m/s    "
    #             f"SNR={snr:.2f}    "
    #         )
    #         f.write(points_str + '\n')
    #
    #     return {'x':x, 'y':y, 'z':z, 'v':speed, 'SNR':snr}

    def jiexi_targets(self, pretty_hex, current_frame_timestamp):
        pretty_hex = pretty_hex.replace(" ", "")
        head_match = self.extract_head(pretty_hex)
        if head_match is not None:
            points_num = self.jiexi_head(head_match)
            bpoint_matches = self.extract_bpoint(pretty_hex, gt_points_num=points_num)
            if bpoint_matches is not None and bpoint_matches != []:
                points_data = []
                for bpoint_match in bpoint_matches:
                    point_data = self.jiexi_bpoint(bpoint_match, current_frame_timestamp)
                    if point_data is not None:
                        points_data.append(point_data)
                return points_data
            else:
                return None
        else:
            return None

    def get_serial_data(self, file):
        if self.com_port is None:
            return None, None

        try:
            n = self.com_port.in_waiting
        except Exception as e:
            # 串口异常，稍等并返回
            print("串口读取异常:", e)
            time.sleep(0.01)
            return None, None

        get_pkg = 0
        header_start_str = bytes.fromhex("dcffeeff")
        point_stop_str = bytes.fromhex("c4feddff")

        if n:
            try:
                temp1 = self.com_port.read(n)
            except Exception as e:
                print("串口 read 异常:", e)
                return None, None

            # 累加到 buffer
            self.buffer += temp1

            # 防止 buffer 过大（异常数据流导致）
            if len(self.buffer) > self._buffer_max_bytes:
                print("⚠️ self.buffer 超出阈值，重置")
                self.buffer = b""

            # 查找数据包起始
            pkg_start = self.buffer.find(header_start_str)
            if pkg_start >= 0:
                self.buffer = self.buffer[pkg_start:]  # 截掉包前无效内容

            # 查找数据包结束
            pkg_stop = self.buffer.find(point_stop_str)
            if pkg_start >= 0 and pkg_stop > 0:
                end_index = pkg_stop + len(point_stop_str)
                one_packet = self.buffer[:end_index]  # 拿到当前包
                self.buffer = self.buffer[end_index:]  # 从缓冲中移除这个包

                get_pkg = 1

        if get_pkg:
            hex_list = [f"{byte:02X}" for byte in one_packet]
            pretty_hex = ' '.join(hex_list)
            now_time = datetime.datetime.now()
            timestamp_str = now_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            timestamp = int(now_time.timestamp() * 1000)
            data_hex = one_packet.hex()
            log_message = (
                f"[{timestamp_str}] # RECV HEX/{len(data_hex)} from {self.com_port.port} <<<\n"
                f"{pretty_hex}\n"
            )

            # file 可能是 None，只有 file 不为 None 才写文件
            if file is not None:
                file.write(log_message)

            # 解析处理
            points_data = self.jiexi_targets(pretty_hex, str(timestamp))
            # 数据格式：
            # [{'x': -0.4620607418087591, 'y': 0.12380880262178248, 'z': 0.49726376428662056, 'v': 0.0, 'SNR': 17768},
            #  {'x': -0.4309006971321849, 'y': 0.11545949381457672, 'z': 0.5263968982612632, 'v': 0.05, 'SNR': 17354}]
            return timestamp, points_data

        return None, None

    def run(self):
        while self.running:
            try:
                timestamp, data = self.get_serial_data(None)  # 不再写文件
                if data is not None:
                    radar_queue.put({'timestamp': timestamp, 'frame': data}, timeout=1)
            except queue.Full:
                # 丢弃旧帧，防止队列爆
                try:
                    _ = radar_queue.get_nowait()
                    radar_queue.put({'timestamp': timestamp, 'frame': data})
                except Exception:
                    pass
            except Exception as e:
                print("Radar thread error:", e)
                time.sleep(0.01)
            time.sleep(0.002)

    def stop(self):
        self.running = False

# 视频数据采集线程
class CameraCapture(threading.Thread):
    def __init__(self, camera_images_save_dir):
        super().__init__()
        self.running = True
        self.camera_images_save_dir = camera_images_save_dir
        # create_or_clear_directory(self.camera_images_save_dir)
        self.cap = cv2.VideoCapture(0)
        self._put_timeout = 0.2
        # self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        # self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    def run(self):
        if not self.cap.isOpened():
            print("无法打开摄像头")
            return
        print("摄像头采集中...按 Q 退出")

        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.01)
                continue
            frame = cv2.flip(frame, 1)
            now_time = datetime.datetime.now()
            timestamp = int(now_time.timestamp() * 1000)

            # 放入队列之前做容量控制：若满则丢弃最旧一帧以腾出空间
            try:
                try:
                    frame_queue.put({'timestamp': timestamp, 'frame': frame}, timeout=self._put_timeout)
                except queue.Full:
                    # 丢弃最旧一帧再尝试放入
                    try:
                        _ = frame_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        frame_queue.put_nowait({'timestamp': timestamp, 'frame': frame})
                    except queue.Full:
                        # 还是满则放弃本帧
                        pass
            except Exception as e:
                # 捕获任何意外，避免线程退出
                print("CameraCapture queue error:", e)

    def stop(self):
        self.running = False

# 同步毫米波数据与视频数据
class SyncFrameCollect(threading.Thread):
    def __init__(self, align_data_save_dir, frame_queue, radar_queue, sync_frame_queue, sync_radar_queue, max_time_diff=100):
        super().__init__()
        self.align_data_save_dir = align_data_save_dir
        # 如果不保存同步文件可以注释下面这一行，避免频繁创建目录
        # create_or_clear_directory(self.align_data_save_dir)
        self.frame_queue = frame_queue
        self.radar_queue = radar_queue
        self.sync_frame_queue = sync_frame_queue
        self.sync_radar_queue = sync_radar_queue
        self.max_time_diff = max_time_diff  # 时间戳容差（单位：毫秒）
        self.frame_buffer = []              # 临时缓存图像帧
        self.running = True

        # 限制 frame_buffer 最大长度，防止无限增长
        self.MAX_FRAME_BUFFER = 60  # 可根据相机/雷达速率调整
        # 如果 frame_buffer 超过 N 秒未匹配则丢弃
        self.FRAME_MAX_AGE_MS = 2000  # 2 秒

    def stop(self):
        self.running = False

    def _prune_frame_buffer(self, radar_ts):
        # 1) 根据时间戳窗口过滤
        self.frame_buffer = [
            f for f in self.frame_buffer
            if abs(radar_ts - f['timestamp']) <= self.max_time_diff or (radar_ts - f['timestamp']) <= 0
        ]
        # 2) 清理超过最大长度（丢弃最旧）
        while len(self.frame_buffer) > self.MAX_FRAME_BUFFER:
            self.frame_buffer.pop(0)
        # 3) 丢弃过老帧（年龄判断）
        now_ts = int(datetime.datetime.now().timestamp() * 1000)
        self.frame_buffer = [
            f for f in self.frame_buffer
            if now_ts - f['timestamp'] <= self.FRAME_MAX_AGE_MS
        ]

    def run(self):
        while self.running:
            # 尽量把相机队列里最新的帧读进 frame_buffer，非阻塞
            try:
                while True:
                    frame_data = self.frame_queue.get_nowait()
                    # append newest at end
                    self.frame_buffer.append(frame_data)
                    # 控制最大长度（避免 burst 时内存跑高）
                    if len(self.frame_buffer) > self.MAX_FRAME_BUFFER:
                        self.frame_buffer.pop(0)
            except queue.Empty:
                pass  # 没有新图像帧

            # 获取一帧毫米波数据（非阻塞）
            try:
                radar_data = self.radar_queue.get_nowait()
            except queue.Empty:
                # 如果长时间没有雷达帧，清理过老的相机帧
                if len(self.frame_buffer) > 0:
                    now_ts = int(datetime.datetime.now().timestamp() * 1000)
                    # 丢弃超过 FRAME_MAX_AGE_MS 的帧
                    self.frame_buffer = [f for f in self.frame_buffer if now_ts - f['timestamp'] <= self.FRAME_MAX_AGE_MS]
                time.sleep(0.005)
                continue

            radar_ts = radar_data['timestamp']

            # 清理太旧或无效的图像帧
            self._prune_frame_buffer(radar_ts)

            # 匹配时间戳最接近的图像帧
            best_match = None
            best_diff = float('inf')
            for frame in self.frame_buffer:
                diff = abs(frame['timestamp'] - radar_ts)
                if diff < best_diff and diff <= self.max_time_diff:
                    best_match = frame
                    best_diff = diff

            # 同步成功，输出两帧到同步队列（并保护 sync 队列）
            if best_match:
                try:
                    try:
                        self.sync_frame_queue.put(best_match, timeout=0.2)
                        self.sync_radar_queue.put(radar_data, timeout=0.2)
                    except queue.Full:
                        # 若同步队列满，丢弃最旧一项再放入
                        try:
                            _ = self.sync_frame_queue.get_nowait()
                        except Exception:
                            pass
                        try:
                            _ = self.sync_radar_queue.get_nowait()
                        except Exception:
                            pass
                        try:
                            self.sync_frame_queue.put_nowait(best_match)
                            self.sync_radar_queue.put_nowait(radar_data)
                        except queue.Full:
                            # 极端情况直接放弃
                            pass
                except Exception as e:
                    print("SyncFrameCollect sync queue put error:", e)

                # 从缓存中移除匹配图像帧
                try:
                    self.frame_buffer.remove(best_match)
                except ValueError:
                    pass

                # 如果需要保存，请在这里调用 save()，目前已注释以避免大量I/O
                # self.save(best_match, radar_data)
            else:
                # 没有匹配的图像帧（可选打印或计数）
                # print("没有匹配的图像帧")
                pass

            # 降低循环速率，避免 100% CPU
            time.sleep(0.002)

    def save(self, camera_data, radar_data):
        img_data = camera_data['frame']
        camera_timestamp = camera_data['timestamp']
        points_data = radar_data['frame']
        radar_timestamp = radar_data['timestamp']
        align_data_save_full_dir = os.path.join(self.align_data_save_dir, f'frame_{radar_timestamp}ms')
        create_or_clear_directory(align_data_save_full_dir)
        align_radar_data_save_path = os.path.join(align_data_save_full_dir, f'frame_{radar_timestamp}ms.txt')
        align_camera_data_save_path = os.path.join(align_data_save_full_dir, f'frame_{camera_timestamp}ms.png')
        point_number = len(points_data)
        for point_data in points_data:
            x, y, z, speed, snr = point_data['x'], point_data['y'], point_data['z'], point_data['v'], point_data['SNR']
            with open(align_radar_data_save_path, 'a') as f:
                points_str = (
                    f"P {point_number}    "
                    f"X={str(x)[:str(x).find('.') + 4]}m    "
                    f"Y={str(y)[:str(y).find('.') + 4]}m    "
                    f"Z={str(z)[:str(z).find('.') + 4]}m    "
                    f"V={str(speed)[:str(speed).find('.') + 3]}m/s    "
                    f"SNR={snr:.2f}    "
                )
                f.write(points_str + '\n')
        cv2.imwrite(align_camera_data_save_path, img_data)

# 聚类和可视化同步帧（matplotlib实现）
class RadarVisualizer:
    def __init__(self,
                 sync_frame_queue,
                 sync_radar_queue,
                 win_frame_num=5,
                 is_enable_data_process=True,
                 is_enable_video_saved=False,
                 video_save_path='output_1.mp4'):

        self.sync_frame_queue = sync_frame_queue
        self.sync_radar_queue = sync_radar_queue
        self.win_frame_num = win_frame_num
        self.is_enable_data_process = is_enable_data_process
        self.is_enable_video_saved = is_enable_video_saved
        self.video_save_path = video_save_path

        self.frame_window = deque(maxlen=win_frame_num)
        self.radar_window = deque(maxlen=win_frame_num)

    def data_process(self):
        try:
            frame_data = self.sync_frame_queue.get(timeout=1)
            radar_data = self.sync_radar_queue.get(timeout=1)

            # diff = abs(frame_data['timestamp'] - radar_data['timestamp'])
            # print("图像帧和毫米波帧时间戳相差：%dms" % diff)

            self.frame_window.append(frame_data['frame'])
            self.radar_window.append(radar_data['frame'])

            if len(self.radar_window) < self.win_frame_num:
                return [], [], [], None

            points = []
            for radar_frame in self.radar_window:
                for p in radar_frame:
                    if p['y'] >= 0:
                        points.append([p['x'], p['y'], p['z']])

            if not points:
                return [], [], [], None

            image = cv2.cvtColor(self.frame_window[-1], cv2.COLOR_BGR2RGB)

            filtered_points = cluster_filter(points)
            X, Y, Z = filtered_points[:, 0], filtered_points[:, 1], filtered_points[:, 2]
            return X, Y, Z, image

        except queue.Empty:
            print("等待同步数据超时")
            return [], [], [], None

    def run(self):
        fig = plt.figure(figsize=(12, 8))
        ax_img = fig.add_subplot(231)
        ax_3d = fig.add_subplot(232, projection="3d")
        ax_xy = fig.add_subplot(234)
        ax_yz = fig.add_subplot(235)
        ax_xz = fig.add_subplot(236)

        image_obj = ax_img.imshow(np.zeros((480, 640, 3), dtype=np.uint8))
        ax_img.axis("off")

        sc_xy = ax_xy.scatter([], [], c='b', s=2)
        sc_yz = ax_yz.scatter([], [], c='g', s=2)
        sc_xz = ax_xz.scatter([], [], c='r', s=2)
        sc_3d = ax_3d.scatter([], [], [], c='m', s=2)

        ax_3d.set_xlim([-3, 3])
        ax_3d.set_ylim([0, 6])
        ax_3d.set_zlim([-2, 2])
        ax_3d.set_xlabel("X (m)")
        ax_3d.set_ylabel("Y (m)")
        ax_3d.set_zlabel("Z (m)")

        ax_xy.set_xlim([-3, 3])
        ax_xy.set_ylim([0, 6])
        ax_xy.set_xlabel("X (m)")
        ax_xy.set_ylabel("Y (m)")

        ax_yz.set_xlim([0, 6])
        ax_yz.set_ylim([-2, 2])
        ax_yz.set_xlabel("Y (m)")
        ax_yz.set_ylabel("Z (m)")

        ax_xz.set_xlim([-3, 3])
        ax_xz.set_ylim([-2, 2])
        ax_xz.set_xlabel("X (m)")
        ax_xz.set_ylabel("Z (m)")

        writer = None
        if self.is_enable_video_saved:
            writer = FFMpegWriter(fps=20, metadata={'artist': 'RadarVisualizer'})
            writer.setup(fig, self.video_save_path, dpi=100)

        def update(_):
            if not self.is_enable_data_process:
                try:
                    frame_data = self.sync_frame_queue.get(timeout=1)
                    radar_data = self.sync_radar_queue.get(timeout=1)
                    # diff = abs(frame_data['timestamp'] - radar_data['timestamp'])
                    # print("图像帧和毫米波帧时间戳相差：%dms" % diff)

                    frame = frame_data['frame']
                    radar_frame = radar_data['frame']
                    image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    X, Y, Z = zip(*[(p['x'], p['y'], p['z']) for p in radar_frame if p['y'] >= 0]) if radar_frame else ([], [], [])
                except queue.Empty:
                    return
            else:
                X, Y, Z, image = self.data_process()
                if len(X) == 0 or image is None:
                    return

            image_obj.set_data(image)
            sc_xy.set_offsets(np.column_stack((X, Y)))
            sc_yz.set_offsets(np.column_stack((Y, Z)))
            sc_xz.set_offsets(np.column_stack((X, Z)))
            sc_3d._offsets3d = (X, Y, Z)

            ax_img.set_title(f"points number: {len(X)}")

            if self.is_enable_video_saved:
                writer.grab_frame()

            return image_obj, sc_xy, sc_yz, sc_xz

        ani = animation.FuncAnimation(fig, update, interval=10, blit=False)

        plt.tight_layout()
        plt.show()

        if self.is_enable_video_saved:
            writer.finish()
            print("视频保存完成！")

# 聚类和可视化同步帧（cv实现）
class RadarVisualizerPG(QtWidgets.QMainWindow):
    def __init__(self,
                 sync_frame_queue,
                 sync_radar_queue,
                 win_frame_num=1,
                 is_enable_data_process=True):

        super().__init__()
        self.sync_frame_queue = sync_frame_queue
        self.sync_radar_queue = sync_radar_queue
        self.win_frame_num = win_frame_num
        self.is_enable_data_process = is_enable_data_process

        self.frame_window = deque(maxlen=win_frame_num)
        self.radar_window = deque(maxlen=win_frame_num)

        self.init_ui()
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_visualization)
        self.timer.start()

    def init_ui(self, size_rate = 2):
        self.setWindowTitle("Radar Visualizer with PyQtGraph")
        self.resize(1400, 800)

        central_widget = QtWidgets.QWidget()
        layout = QtWidgets.QGridLayout(central_widget)

        # 图像显示
        self.img_label = QtWidgets.QLabel()
        self.img_label.setFixedSize(640, 480)
        layout.addWidget(self.img_label, 0, 0, 2, 1)

        # 点云视图（XY、XZ、YZ）
        self.xy_plot = pg.PlotWidget(title="XY View")
        self.xy_plot.setXRange(-3, 3)
        self.xy_plot.setYRange(0, 6)
        self.xy_scatter = pg.ScatterPlotItem(pen=None, symbol='o', size=3*size_rate, brush='b')
        self.xy_plot.addItem(self.xy_scatter)
        layout.addWidget(self.xy_plot, 0, 1)

        self.yz_plot = pg.PlotWidget(title="YZ View")
        self.yz_plot.setXRange(0, 6)
        self.yz_plot.setYRange(-2, 2)
        self.yz_scatter = pg.ScatterPlotItem(pen=None, symbol='o', size=3*size_rate, brush='g')
        self.yz_plot.addItem(self.yz_scatter)
        layout.addWidget(self.yz_plot, 0, 2)

        self.xz_plot = pg.PlotWidget(title="XZ View")
        self.xz_plot.setXRange(-3, 3)
        self.xz_plot.setYRange(-2, 2)
        self.xz_scatter = pg.ScatterPlotItem(pen=None, symbol='o', size=3*size_rate, brush='r')
        self.xz_plot.addItem(self.xz_scatter)
        layout.addWidget(self.xz_plot, 1, 1)

        # 3D视图
        self.view_3d = gl.GLViewWidget()
        self.view_3d.opts['distance'] = 10
        self.view_3d.setCameraPosition(elevation=30, azimuth=45)
        self.pts_3d = gl.GLScatterPlotItem(pos=np.zeros((1, 3)), color=(1, 0, 1, 1), size=3*size_rate)
        self.view_3d.addItem(self.pts_3d)
        layout.addWidget(self.view_3d, 1, 2)

        self.setCentralWidget(central_widget)

    def data_process(self):
        try:
            frame_data = self.sync_frame_queue.get(timeout=1)
            radar_data = self.sync_radar_queue.get(timeout=1)
            self.frame_window.append(frame_data['frame'])
            self.radar_window.append(radar_data['frame'])

            if len(self.radar_window) < self.win_frame_num:
                return [], [], [], None

            points = []
            for radar_frame in self.radar_window:
                for p in radar_frame:
                    if p['y'] >= 0:
                        points.append([p['x'], p['y'], p['z']])

            if not points:
                return [], [], [], None

            image = cv2.cvtColor(self.frame_window[-1], cv2.COLOR_BGR2RGB)
            filtered_points = cluster_filter(points)
            X, Y, Z = filtered_points[:, 0], filtered_points[:, 1], filtered_points[:, 2]
            return X, Y, Z, image

        except queue.Empty:
            print("等待同步数据超时")
            return [], [], [], None

    def update_visualization(self):
        X, Y, Z, image = self.data_process() if self.is_enable_data_process else ([], [], [], None)
        if image is not None:
            img = QtGui.QImage(image.data, image.shape[1], image.shape[0], image.strides[0],
                               QtGui.QImage.Format_RGB888)
            self.img_label.setPixmap(QtGui.QPixmap.fromImage(img))

        if len(X) == 0:
            return

        self.xy_scatter.setData(X, Y)
        self.yz_scatter.setData(Y, Z)
        self.xz_scatter.setData(X, Z)
        points_3d = np.vstack((X, Y, Z)).T
        self.pts_3d.setData(pos=points_3d)


# 聚类和可视化同步帧（cv实现）
class RadarVisualizerPG2(QtWidgets.QMainWindow):
    def __init__(self,
                 sync_frame_queue,
                 sync_radar_queue,
                 win_frame_num=3,
                 is_enable_data_process=True):

        super().__init__()
        self.sync_frame_queue = sync_frame_queue
        self.sync_radar_queue = sync_radar_queue
        self.win_frame_num = win_frame_num
        self.is_enable_data_process = is_enable_data_process
        create_or_clear_directory(radar_points_dbscan_txt_save_dir)
        create_or_clear_directory(radar_points_dbscan_bin_save_dir)
        self.frame_window = deque(maxlen=win_frame_num)
        self.radar_window = deque(maxlen=win_frame_num)

        self.bin_counter = 1

        self.init_ui()
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_visualization)
        self.timer.start()

    def init_ui(self, size_rate=2):
        self.setWindowTitle("Radar Visualizer with PyQtGraph")
        self.resize(1400, 800)

        central_widget = QtWidgets.QWidget()
        layout = QtWidgets.QGridLayout(central_widget)

        # 图像显示
        self.img_label = QtWidgets.QLabel()
        self.img_label.setFixedSize(640, 480)
        layout.addWidget(self.img_label, 0, 0, 2, 1)

        # 点云视图（XY、XZ、YZ）
        self.xy_plot = pg.PlotWidget(title="XY View")
        self.xy_plot.setBackground('k')
        self.xy_plot.setXRange(-3, 3)
        self.xy_plot.setYRange(0, 6)
        self.xy_plot.setLabel(axis="left", text="Y(m)")
        self.xy_plot.setLabel(axis="bottom", text="X(m)")
        self.xy_scatter = pg.ScatterPlotItem(pen=pg.mkPen('w'), symbol='o', size=5*size_rate, brush='b')
        self.xy_plot.addItem(self.xy_scatter)
        self.xy_plot.showGrid(x=True, y=True)
        layout.addWidget(self.xy_plot, 0, 1)

        self.yz_plot = pg.PlotWidget(title="YZ View")
        self.yz_plot.setBackground('k')
        self.yz_plot.setXRange(0, 6)
        self.yz_plot.setYRange(-2, 2)
        self.yz_plot.setLabel(axis="left", text="Z(m)")
        self.yz_plot.setLabel(axis="bottom", text="Y(m)")
        self.yz_scatter = pg.ScatterPlotItem(pen=pg.mkPen('w'), symbol='o', size=5*size_rate, brush='g')
        self.yz_plot.addItem(self.yz_scatter)
        self.yz_plot.showGrid(x=True, y=True)
        layout.addWidget(self.yz_plot, 0, 2)

        self.xz_plot = pg.PlotWidget(title="XZ View")
        self.xz_plot.setBackground('k')
        self.xz_plot.setXRange(-3, 3)
        self.xz_plot.setYRange(-3, 3)
        self.xz_plot.setLabel(axis="left", text="Z(m)")
        self.xz_plot.setLabel(axis="bottom", text="X(m)")
        self.xz_scatter = pg.ScatterPlotItem(pen=pg.mkPen('w'), symbol='o', size=5*size_rate, brush='r')
        self.xz_plot.addItem(self.xz_scatter)
        self.xz_plot.showGrid(x=True, y=True)
        layout.addWidget(self.xz_plot, 1, 1)

        # 3D视图
        # ---------- 3D点云显示 ----------
        # 创建3D点云显示组件
        self.gl_widget = gl.GLViewWidget()
        self.gl_widget.setBackgroundColor('k')

        # 创建3D散点图项
        self.plot3d = gl.GLScatterPlotItem(color=(1, 0, 0, 1), size=5 * size_rate)
        self.gl_widget.addItem(self.plot3d)

        # 设置3D网格尺寸
        width = 5
        length = 5
        height = 3

        # 创建Z平面网格
        gz = gl.GLGridItem()
        gz.setSize(x=width, y=length, z=0)
        gz.rotate(90, 0, 0, 1)
        gz.translate(0, length / 2, -height / 2)
        self.gl_widget.addItem(gz)

        # 创建Y平面网格
        gy = gl.GLGridItem()
        gy.setSize(x=height, y=width, z=0)
        gy.rotate(90, 1, 0, 0)
        gy.rotate(90, 0, 1, 0)
        gy.translate(0, 0, 0)
        self.gl_widget.addItem(gy)

        # 创建X平面网格
        gx = gl.GLGridItem()
        gx.setSize(x=length, y=height, z=0)
        gx.rotate(90, 0, 1, 0)
        gx.rotate(90, 1, 0, 0)
        gx.translate(-length / 2, length / 2, 0)
        self.gl_widget.addItem(gx)

        # 设置3D视图相机位置
        center_x = 0
        center_y = 1.5
        center_z = -1
        camera_position = self.gl_widget.cameraPosition()
        camera_position[0] = center_x
        camera_position[1] = center_y
        camera_position[2] = center_z
        self.gl_widget.setCameraPosition(
            distance=5,  # 相机远近
            elevation=3,  # 上下旋转视角
            azimuth=98,  # 左右旋转视角
            pos=camera_position
        )
        self.setCentralWidget(central_widget)

        # 添加3D空间参考点
        points = [
            {"pos": (-3, 0, 2.0), "label": "(-3, 0, 2.0)"},
            {"pos": (-3, 6, -2.0), "label": "(-3, 6, -2.0)"},
            {"pos": (3, 0, -2.0), "label": "(3, 0, -2.0)"},
        ]
        for point in points:
            pos = point["pos"]
            label = point["label"]
            color = (255, 255, 255)
            text_item = gl.GLTextItem(
                text=label, color=color, pos=pos, font=QtGui.QFont("Helvetica", 10)
            )
            self.gl_widget.addItem(text_item)
        layout.addWidget(self.gl_widget, 1, 2)

        # 状态显示：目标状态与跌倒状态
        self.status_label = QtWidgets.QLabel("状态：")
        self.status_label.setStyleSheet("font-size: 18px; color: blue")
        layout.addWidget(self.status_label, 2, 0, 1, 3)
        xy_container = QtWidgets.QWidget()
        xy_layout = QtWidgets.QVBoxLayout(xy_container)
        xy_layout.setContentsMargins(0, 0, 0, 0)  # 去掉边距，防止挤压
        xy_layout.setSpacing(5)

        # 添加 XY 图到这个小布局
        xy_layout.addWidget(self.xy_plot)

        # 添加“范围内人数”状态栏
        self.people_status_label = QtWidgets.QLabel("范围内人数：0 | 实时跌倒人数：0")
        self.people_status_label.setStyleSheet("font-size: 18px; color: darkgreen")
        xy_layout.addWidget(self.people_status_label)

        # 将这个容器放入主 layout 原来放 XY 图的位置
        layout.addWidget(xy_container, 0, 1)
        self.setCentralWidget(central_widget)

    def data_process(self):
        try:
            frame_data = self.sync_frame_queue.get(timeout=1)
            radar_data = self.sync_radar_queue.get(timeout=1)
            self.frame_window.append(frame_data['frame'])
            self.radar_window.append(radar_data['frame'])

            if len(self.radar_window) < self.win_frame_num:
                return [], [], [], [], [], None

            points = []
            for radar_frame in self.radar_window:
                for p in radar_frame:
                    if p['y'] >= 0:
                        points.append([p['x'], p['y'], p['z'], p['v'], p['SNR']])

            if not points:
                return [], [], [], [], [], None

            image = cv2.cvtColor(self.frame_window[-1], cv2.COLOR_BGR2RGB)
            # filtered_points = cluster_filter(points)
            filtered_points = np.array(points)
            X, Y, Z, v, SNR = filtered_points[:, 0], filtered_points[:, 1], filtered_points[:, 2], filtered_points[:, 3], filtered_points[:, 4]
            return X, Y, Z, v, SNR, image

        except queue.Empty:
            print("等待同步数据超时")
            return [], [], [], [], [], None

    def create_voxel(self, x, y, z, size=0.08, color=(1.0, 0.0, 0.0, 1.0)):

        # 创建立方体 MeshData
        s = size / 2
        verts = np.array([
            [x - s, y - s, z - s],
            [x + s, y - s, z - s],
            [x + s, y + s, z - s],
            [x - s, y + s, z - s],
            [x - s, y - s, z + s],
            [x + s, y - s, z + s],
            [x + s, y + s, z + s],
            [x - s, y + s, z + s],
        ])

        faces = np.array([
            [0, 1, 2], [0, 2, 3],
            [4, 5, 6], [4, 6, 7],
            [0, 1, 5], [0, 5, 4],
            [2, 3, 7], [2, 7, 6],
            [1, 2, 6], [1, 6, 5],
            [0, 3, 7], [0, 7, 4],
        ])

        mesh = MeshData(vertexes=verts, faces=faces)
        voxel = GLMeshItem(meshdata=mesh, color=color, smooth=False, drawEdges=False, shader='shaded')
        return voxel

    def update_visualization(self):
        X, Y, Z, v, SNR, image = self.data_process() if self.is_enable_data_process else ([], [], [], [], [], None)

        if image is not None:
            img = QtGui.QImage(image.data, image.shape[1], image.shape[0], image.strides[0],
                               QtGui.QImage.Format_RGB888)
            self.img_label.setPixmap(QtGui.QPixmap.fromImage(img))

        if len(X) == 0:
            return

        # ---------- 配色表 ----------
        color_table = ['r', 'g', 'b', 'c', 'm', 'y', 'w', 'orange', 'purple', 'lime', 'pink']

        # ---------- 通用函数：根据聚类结果生成 spots ----------
        def create_cluster_spots(points, labels, color_table):
            spots = []
            unique_labels = set(labels)
            unique_labels.discard(-1)
            for label in unique_labels:
                color = 'gray' if label == -1 else color_table[label % len(color_table)]
                cluster_points = points[labels == label]
                for pt in cluster_points:
                    spots.append({'pos': pt, 'brush': color, 'size': 10})
            return spots

        # ---------- XY 聚类及显示 ----------
        points_xy = np.vstack((X, Y)).T
        labels_xy = DBSCAN(eps=0.4, min_samples=30).fit(points_xy).labels_

        unique_labels = set(labels_xy)
        unique_labels.discard(-1)  # 去掉噪声标签

        centers = []
        clusters_Z = []
        clusters_X = []
        clusters_Y = []

        for label in unique_labels:
            cluster_points = points_xy[labels_xy == label]
            center = cluster_points.mean(axis=0)
            centers.append(center)
            clusters_Z.append(Z[labels_xy == label])
            clusters_X.append(X[labels_xy == label])
            clusters_Y.append(Y[labels_xy == label])
        centers = np.array(centers).reshape(-1, 2)

        # 画大红/绿点（中心）
        spots_center = []
        fall_count = 0
        for center, cluster_Z in zip(centers, clusters_Z):
            fall_detected = (np.max(cluster_Z) < -0.4 and (np.max(cluster_Z) - np.min(cluster_Z)) < 0.5)
            color = 'r' if fall_detected else 'g'
            if fall_detected:
                fall_count += 1
            spots_center.append({'pos': center, 'brush': color, 'size': 20})

        if hasattr(self, 'xy_center_scatter'):
            self.xy_plot.removeItem(self.xy_center_scatter)
        self.xy_center_scatter = pg.ScatterPlotItem(spots=spots_center, pen=pg.mkPen(None), symbol='o')
        self.xy_plot.addItem(self.xy_center_scatter)

        # ---------- YZ 聚类及显示 ----------
        points_yz = np.vstack((Y, Z)).T
        labels_yz = DBSCAN(eps=0.4, min_samples=20).fit(points_yz).labels_
        spots_yz = create_cluster_spots(points_yz, labels_yz, color_table)

        if hasattr(self, 'yz_scatter_cluster'):
            self.yz_plot.removeItem(self.yz_scatter_cluster)
        self.yz_scatter_cluster = pg.ScatterPlotItem(spots=spots_yz, pen=pg.mkPen(None), symbol='o')
        self.yz_plot.addItem(self.yz_scatter_cluster)

        # ---------- XZ 聚类及显示 ----------
        points_xz = np.vstack((X, Z)).T
        labels_xz = DBSCAN(eps=0.4, min_samples=25).fit(points_xz).labels_
        spots_xz = create_cluster_spots(points_xz, labels_xz, color_table)

        if hasattr(self, 'xz_scatter_cluster'):
            self.xz_plot.removeItem(self.xz_scatter_cluster)
        self.xz_scatter_cluster = pg.ScatterPlotItem(spots=spots_xz, pen=pg.mkPen(None), symbol='o')
        self.xz_plot.addItem(self.xz_scatter_cluster)
        # ---------- 3d聚类及显示 ----------
        points_3d = np.vstack((X, Y, Z)).T
        colors_rgba = []
        for z in Z:
            if z < -1.0:
                # 灰色
                colors_rgba.append([0.5, 0.5, 0.5, 1.0])
            elif z < -0.5:
                # 灰 -> 红 渐变
                t = (z - (-1.0)) / (0.5)  # 从 -1.0 到 -0.5
                r = 0.5 + t * (1.0 - 0.5)  # R: 0.5 -> 1.0
                g = 0.5 + t * (0.0 - 0.5)  # G: 0.5 -> 0.0
                b = 0.5 + t * (0.0 - 0.5)  # B: 0.5 -> 0.0
                colors_rgba.append([r, g, b, 1.0])
            elif z < -0.2:
                # 红 -> 黄 渐变
                t = (z - (-0.5)) / (0.3)  # 从 -0.5 到 -0.2
                r = 1.0
                g = 0.0 + t * (1.0 - 0.0)  # G: 0.0 -> 1.0
                b = 0.0
                colors_rgba.append([r, g, b, 1.0])
            else:
                # 黄 -> 绿 渐变
                t = (z - (-0.2)) / (max(Z) - (-0.2))
                r = 1.0 - t * (1.0 - 0.0)  # R: 1.0 -> 0.0
                g = 1.0
                b = 0.0
                colors_rgba.append([r, g, b, 1.0])

        colors_rgba = np.array(colors_rgba)

        # 聚类去噪
        labels = DBSCAN(eps=0.5, min_samples=30).fit(points_3d).labels_
        mask = labels != -1
        filtered_points = points_3d[mask]
        filtered_vs = v[mask]
        filtered_snrs = SNR[mask]


        filtered_colors = colors_rgba[mask]
        # ================== 【改动 1】删除立方体循环绘制 ==================
        # 原来这里有 create_voxel 循环，现在改成一次性 GLScatterPlotItem 绘制
        voxel_size = 0.1
        voxel_size_offset = 0.8
        filtered_points = np.round(filtered_points / voxel_size) * voxel_size
        # 删除旧绘制
        if hasattr(self, "scatter_item"):
            self.gl_widget.removeItem(self.scatter_item)
        if hasattr(self, "shadow_item"):
            self.gl_widget.removeItem(self.shadow_item)

        # ================== 【改动 2】主点云绘制 ==================
        if not hasattr(self, "scatter_item"):
            self.scatter_item = GLScatterPlotItem(
                pos=filtered_points,
                size=voxel_size * voxel_size_offset,  # 单位改成像素
                color=filtered_colors,
                pxMode=False
            )
        else:
            self.scatter_item.setData(pos=filtered_points, color=filtered_colors)
        self.scatter_item.setGLOptions('opaque')
        # self.scatter_item.setGLOptions('additive')  # 颜色叠加模式

        self.gl_widget.addItem(self.scatter_item)

        # ================== 【改动 3】投影绘制 ==================
        projection_points = filtered_points.copy()
        projection_points[:, 2] = -1.5  # 固定在原来投影高度
        shadow_color = (0.5, 0.5, 0.5, 0.3)  # 灰色半透明
        shadow_colors = np.tile(shadow_color, (projection_points.shape[0], 1))

        self.shadow_item = GLScatterPlotItem(
            pos=projection_points,  # 投影点
            size=voxel_size * voxel_size_offset,  # 与主点云大小一致
            color=shadow_colors,  # 灰色半透明
            pxMode=False
        )
        self.shadow_item.setGLOptions('opaque')

        self.gl_widget.addItem(self.shadow_item)



        # ---------- 人数统计（基于 XY 聚类） ----------
        unique_labels_xy = set(labels_xy)
        unique_labels_xy.discard(-1)
        centers = []
        for label in unique_labels_xy:
            cluster_points = points_xy[labels_xy == label]
            center = cluster_points.mean(axis=0)
            centers.append(center)
        centers = np.array(centers).reshape(-1, 2)

        xmin, xmax = -3, 3
        ymin, ymax = 0, 6
        in_range_mask = (centers[:, 0] >= xmin) & (centers[:, 0] <= xmax) & \
                        (centers[:, 1] >= ymin) & (centers[:, 1] <= ymax)
        in_range_centers = centers[in_range_mask]
        current_count = len(in_range_centers)
        self.people_status_label.setText(f"范围内人数：{current_count} | 实时跌倒人数：{fall_count}")

        # ---------- 全局状态栏判断（原始点云全局） ----------
        target_detected = len(X) > 10
        fall_detected = np.max(Z) < 0.6 and (np.max(Z) - np.min(Z)) < 0.4 and target_detected

        if not target_detected:
            self.status_label.setText("状态：无目标")
            self.status_label.setStyleSheet("font-size: 48px; color: gray")
        elif fall_detected:
            self.status_label.setText("状态：检测到目标 - 疑似跌倒")
            self.status_label.setStyleSheet("font-size: 48px; color: red")
        else:
            self.status_label.setText("状态：检测到目标 - 正常活动")
            self.status_label.setStyleSheet("font-size: 48px; color: green")



def stop_all(*threads):
    for t in threads:
        t.stop()
    for t in threads:
        t.join()

def main():
    camera = CameraCapture(camera_images_save_dir)
    radar = RadarCapture(radar_raw_data_save_path, radar_points_data_save_path, radar_points_data_save_dir)
    sync = SyncFrameCollect(align_data_save_dir, frame_queue, radar_queue, sync_frame_queue, sync_radar_queue, max_time_diff=max_time_diff)
    camera.start()
    radar.start()
    sync.start()
    # 启动可视化
    try:
        if not is_enable_vis_PG:
            visualizer = RadarVisualizer(sync_frame_queue, sync_radar_queue,
                                         win_frame_num=win_frame_num,
                                         is_enable_data_process=is_enable_data_process,
                                         is_enable_video_saved=is_enable_video_saved)
            visualizer.run()
        else:
            app = QtWidgets.QApplication(sys.argv)
            visualizer = RadarVisualizerPG2(sync_frame_queue, sync_radar_queue)
            visualizer.show()
            sys.exit(app.exec_())
    finally:
        # 关闭所有线程
        stop_all(camera, radar, sync)
        print("程序已结束")

if __name__ == "__main__":
    main()
