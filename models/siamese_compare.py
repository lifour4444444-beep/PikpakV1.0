import onnxruntime
import numpy as np
import cv2
from PIL import Image


class ONNXSiamese:

    def __init__(self, ONNX: str) -> None:
        try:
            self.OSession = onnxruntime.InferenceSession(ONNX)
        except Exception as e:
            raise RuntimeError(
                f'Siamese模型加载失败: {ONNX}\n'
                f'错误: {e}\n'
                f'可能原因: 1) 模型文件损坏 2) onnxruntime版本不兼容 3) 缺少VC运行库'
            ) from e

    def Compare(self, Img0: Image.Image, Img1: Image.Image) -> float:
        return self.OSession.run(
            None,
            {
                "X": np.expand_dims(
                    np.transpose(
                        cv2.resize(
                            cv2.cvtColor(
                                cv2.cvtColor(np.asarray(Img0), cv2.COLOR_RGB2BGR),
                                cv2.COLOR_BGR2RGB,
                            ),
                            (120, 120),
                        ).astype(np.float32)
                        / 255,
                        (2, 0, 1),
                    ),
                    axis=0,
                ),
                "Y": np.expand_dims(
                    np.transpose(
                        cv2.resize(
                            cv2.cvtColor(
                                cv2.cvtColor(np.asarray(Img1), cv2.COLOR_RGB2BGR),
                                cv2.COLOR_BGR2RGB,
                            ),
                            (120, 120),
                        ).astype(np.float32)
                        / 255,
                        (2, 0, 1),
                    ),
                    axis=0,
                ),
            },
        )[0][0][0]