import onnxruntime
import numpy as np
import cv2
from PIL import Image
import typing


class YOLOv5:

    def __init__(
        self,
        OnnxPath: str,
        CFThresh: float,
        IOUThresh: float,
        Resize: typing.Optional[typing.Tuple[int, int]] = None,
    ) -> None:
        self.OSession = onnxruntime.InferenceSession(OnnxPath)
        self.InputName = [i.name for i in self.OSession.get_inputs()]
        self.CFThresh = CFThresh
        self.IOUThresh = IOUThresh
        self.Resize = Resize

    def Inference(self, Img: Image.Image) -> np.array:
        if self.Resize:
            Img = Img.resize(self.Resize)
        Img = cv2.cvtColor(np.asarray(Img), cv2.COLOR_RGB2BGR)
        OrigImg = Img
        Img = np.expand_dims(
            OrigImg[:, :, ::-1].transpose(2, 0, 1).astype(dtype=np.float32) / 255.0,
            axis=0,
        )
        return self.OSession.run(None, {i: Img for i in self.InputName})[0]

    def NMS(self, Dets: list) -> list[int]:
        X1 = Dets[:, 0]
        Y1 = Dets[:, 1]
        X2 = Dets[:, 2]
        Y2 = Dets[:, 3]
        Areas = (Y2 - Y1 + 1) * (X2 - X1 + 1)
        Keep = []
        Index = Dets[:, 4].argsort()[::-1]

        while Index.size > 0:
            i = Index[0]
            Keep.append(i)
            X11 = np.maximum(X1[i], X1[Index[1:]])
            Y11 = np.maximum(Y1[i], Y1[Index[1:]])
            X22 = np.minimum(X2[i], X2[Index[1:]])
            Y22 = np.minimum(Y2[i], Y2[Index[1:]])
            Overlaps = np.maximum(0, X22 - X11 + 1) * np.maximum(0, Y22 - Y11 + 1)
            ious = Overlaps / (Areas[i] + Areas[Index[1:]] - Overlaps)
            IDX = np.where(ious <= self.IOUThresh)[0]
            Index = Index[IDX + 1]
        return Keep

    def XYWH_XYXY(self, X: list) -> np.array:
        Y = np.copy(X)
        Y[:, 0] = X[:, 0] - X[:, 2] / 2
        Y[:, 1] = X[:, 1] - X[:, 3] / 2
        Y[:, 2] = X[:, 0] + X[:, 2] / 2
        Y[:, 3] = X[:, 1] + X[:, 3] / 2
        return Y

    def FilterBox(self, OrigBox) -> np.array:
        OrigBox = np.squeeze(OrigBox)
        Box = OrigBox[OrigBox[..., 4] > self.CFThresh]
        CLSInf = Box[..., 5:]
        CLS = []
        for i in range(len(CLSInf)):
            CLS.append(int(np.argmax(CLSInf[i])))
        ALLClasses = list(set(CLS))
        Output = []
        for i in range(len(ALLClasses)):
            CurrCLS = ALLClasses[i]
            CCB = []
            for j in range(len(CLS)):
                if CLS[j] == CurrCLS:
                    Box[j][5] = CurrCLS
                    CCB.append(Box[j][:6])
            CCB = self.XYWH_XYXY(np.array(CCB))
            for k in self.NMS(CCB):
                Output.append(CCB[k])
        Output = np.array(Output)
        return Output

    def NMSv2(self, Boxes, Scores) -> list[int]:
        x1 = Boxes[:, 0]
        y1 = Boxes[:, 1]
        x2 = Boxes[:, 2]
        y2 = Boxes[:, 3]

        areas = (x2 - x1 + 1) * (y2 - y1 + 1)
        order = Scores.argsort()[::-1]

        Keep = []
        while order.size > 0:
            i = order[0]
            Keep.append(i)
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            w = np.maximum(0, xx2 - xx1 + 1)
            h = np.maximum(0, yy2 - yy1 + 1)
            inter = w * h
            iou = inter / (areas[i] + areas[order[1:]] - inter)
            inds = np.where(iou <= self.IOUThresh)[0]
            order = order[inds + 1]

        return Keep

    def Detect(self, Img: Image.Image) -> list:
        BoxData = self.FilterBox(self.Inference(Img))
        if BoxData.shape[0] == 0:
            return []
        Boxes = BoxData[..., :4].astype(np.int32)
        return [Boxes[i] for i in self.NMSv2(Boxes, BoxData[..., 4])]