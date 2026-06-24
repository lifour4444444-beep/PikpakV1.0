import onnxruntime
import numpy as np
import cv2
from PIL import Image


class ONNXSiamese:

    def __init__(self, ONNX: str) -> None:
        self.OSession = onnxruntime.InferenceSession(ONNX)

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