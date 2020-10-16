import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


class IrisBlock(nn.Module):
    """This is the main building block for architecture
    which is just residual block with one dw-conv and max-pool/channel pad
    in the second branch if input size doesn't match output size"""
    def __init__(self, in_channels: int, out_channels: int, kernel_size: int = 3, stride: int = 1):
        super(IrisBlock, self).__init__()
        

        # My impl
        self.stride = stride
        self.channel_pad = out_channels - in_channels
        
        # TFLite uses slightly different padding than PyTorch 
        # on the depthwise conv layer when the stride is 2.
        if stride == 2:
            self.max_pool = nn.MaxPool2d(kernel_size=stride, stride=stride)
            padding = 0
        else:
            padding = (kernel_size - 1) // 2

        self.convs = nn.Sequential(
            nn.Conv2d(in_channels=in_channels, out_channels=int(out_channels/2), kernel_size=stride, stride=1, padding=0, bias=True),
            nn.PReLU(int(out_channels/2)),
            nn.Conv2d(in_channels=int(out_channels/2), out_channels=int(out_channels/2), 
                      kernel_size=kernel_size, stride=stride, padding=padding,  # Padding might be wrong here
                      groups=int(out_channels/2), bias=True),
            nn.Conv2d(in_channels=int(out_channels/2), out_channels=out_channels, kernel_size=1, stride=1, padding=0, bias=True),
        )

        self.act = nn.PReLU(out_channels)

        # # Facemesh impl

        # self.stride = stride
        # self.channel_pad = out_channels - in_channels

        

        # # TFLite uses slightly different padding than PyTorch 
        # # on the depthwise conv layer when the stride is 2.
        # if stride == 2:
        #     self.max_pool = nn.MaxPool2d(kernel_size=stride, stride=stride)
        #     padding = 0
        # else:
        #     padding = (kernel_size - 1) // 2

        # self.convs = nn.Sequential(
        #     nn.Conv2d(in_channels=in_channels, out_channels=in_channels, 
        #               kernel_size=kernel_size, stride=stride, padding=padding, 
        #               groups=in_channels, bias=True),
        #     nn.Conv2d(in_channels=in_channels, out_channels=out_channels, 
        #               kernel_size=1, stride=1, padding=0, bias=True),
        # )

        # self.act = nn.PReLU(out_channels)

    def forward(self, x):
        if self.stride == 2:
            h = F.pad(x, (0, 2, 0, 2), "constant", 0)
            x = self.max_pool(x)
        else:
            h = x

        if self.channel_pad > 0:
            x = F.pad(x, (0, 0, 0, 0, 0, self.channel_pad), "constant", 0)
        
        return self.act(self.convs(h) + x)


class IrisLandmarks(nn.Module):
    """The FaceMesh face landmark model from MediaPipe.

    Because we won't be training this model, it doesn't need to have
    batchnorm layers. These have already been "folded" into the conv 
    weights by TFLite.

    The conversion to PyTorch is fairly straightforward, but there are 
    some small differences between TFLite and PyTorch in how they handle
    padding on conv layers with stride 2.

    This version works on batches, while the MediaPipe version can only
    handle a single image at a time.
    """
    def __init__(self):
        super(IrisLandmarks, self).__init__()

        self.num_coords = 228
        self.x_scale = 64.0
        self.y_scale = 64.0
        self.min_score_thresh = 0.75

        self._define_layers()

    def _define_layers(self):
        self.backbone = nn.Sequential(
            nn.Conv2d(in_channels=3, out_channels=64, kernel_size=3, stride=2, padding=0, bias=True),
            nn.PReLU(64),

            IrisBlock(64, 64),
            IrisBlock(64, 64),
            IrisBlock(64, 64),
            IrisBlock(64, 64),
            IrisBlock(64, 128, stride=2),
            IrisBlock(128, 128),
            IrisBlock(128, 128),
            IrisBlock(128, 128),
            IrisBlock(128, 128),
            IrisBlock(128, 128, stride=2)
        )
        self.split_eye = nn.Sequential(
            IrisBlock(128, 128),
            IrisBlock(128, 128),
            IrisBlock(128, 128, stride=2),
            IrisBlock(128, 128),
            IrisBlock(128, 128),
            IrisBlock(128, 128, stride=2),
            IrisBlock(128, 128),
            IrisBlock(128, 128),
            nn.Conv2d(in_channels=128, out_channels=213, kernel_size=2, stride=1, padding=0, bias=True)
        )
        self.split_iris = nn.Sequential(
            IrisBlock(128, 128),
            IrisBlock(128, 128),
            IrisBlock(128, 128, stride=2),
            IrisBlock(128, 128),
            IrisBlock(128, 128),
            IrisBlock(128, 128, stride=2),
            IrisBlock(128, 128),
            IrisBlock(128, 128),
            nn.Conv2d(in_channels=128, out_channels=15, kernel_size=2, stride=1, padding=0, bias=True)
        )

        
        
    def forward(self, x):
        # TFLite uses slightly different padding on the first conv layer
        # than PyTorch, so do it manually.
        x = nn.ReflectionPad2d((1, 0, 1, 0))(x)
        # x = F.pad(x, (1,2,1,2), "constant", 0)
        b = x.shape[0]      # batch size, needed for reshaping later

        x = self.backbone(x)            # (b, 128, 8, 8)
        
        e = self.split_eye(x)           # (b, 213, 1, 1)    
        e = e.view(b, -1)               # (b, 213)
        
        i = self.split_iris(x)          # (b, 15, 1, 1)
        i = i.reshape(b, -1)            # (b, 15)
        
        return [e, i]
        # # TFLite uses slightly different padding on the first conv layer
        # # than PyTorch, so do it manually.
        # x = nn.ReflectionPad2d((1, 0, 1, 0))(x)
        # b = x.shape[0]      # batch size, needed for reshaping later

        # x = self.backbone(x)            # (b, 128, 6, 6)
        
        # c = self.conf_head(x)           # (b, 1, 1, 1)
        # c = c.view(b, -1)               # (b, 1)
        
        # r = self.coord_head(x)          # (b, 1404, 1, 1)
        # r = r.reshape(b, -1)            # (b, 1404)
        
        # return [r, c]

    def _device(self):
        """Which device (CPU or GPU) is being used by this model?"""
        return self.backbone[0].weight.device
    
    def load_weights(self, path):
        self.load_state_dict(torch.load(path))
        self.eval()        
    
    def _preprocess(self, x):
        """Converts the image pixels to the range [-1, 1]."""
        return x.float() / 127.5 - 1.0

    def predict_on_image(self, img):
        """Makes a prediction on a single image.

        Arguments:
            img: a NumPy array of shape (H, W, 3) or a PyTorch tensor of
                 shape (3, H, W). The image's height and width should be 
                 128 pixels.

        Returns:
            A tensor with face detections.
        """
        if isinstance(img, np.ndarray):
            img = torch.from_numpy(img).permute((2, 0, 1))
        
        return self.predict_on_batch(img.unsqueeze(0))

    def predict_on_batch(self, x):
        """Makes a prediction on a batch of images.

        Arguments:
            x: a NumPy array of shape (b, H, W, 3) or a PyTorch tensor of
               shape (b, 3, H, W). The height and width should be 64 pixels.

        Returns:
            A list containing a tensor of face detections for each image in 
            the batch. If no faces are found for an image, returns a tensor
            of shape (0, 17).

        Each face detection is a PyTorch tensor consisting of 17 numbers:
            - ymin, xmin, ymax, xmax
            - x,y-coordinates for the 6 keypoints
            - confidence score
        """
        if isinstance(x, np.ndarray):
            x = torch.from_numpy(x).permute((0, 3, 1, 2))

        assert x.shape[1] == 3
        assert x.shape[2] == 64
        assert x.shape[3] == 64

        # 1. Preprocess the images into tensors:
        x = x.to(self._device())
        x = self._preprocess(x)

        # 2. Run the neural network:
        with torch.no_grad():
            out = self.__call__(x)

        # 3. Postprocess the raw predictions:
        eye, iris = out
        eye[0:-1:3] *= self.x_scale
        eye[1:-1:3] *= self.y_scale
        iris[0:-1:3] *= self.x_scale
        iris[1:-1:3] *= self.y_scale

        return eye.view(-1, 3), iris.view(-1, 3)