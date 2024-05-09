import argparse
import time
from pathlib import Path

import cv2
import torch
import torch.backends.cudnn as cudnn
from numpy import random

from models.experimental import attempt_load
from utils.datasets import LoadStreams, LoadImages
from utils.general import check_img_size, check_imshow, non_max_suppression, apply_classifier, \
    scale_coords, xyxy2xywh, strip_optimizer, set_logging, increment_path
from utils.plots import plot_one_box
from utils.torch_utils import select_device, load_classifier, time_synchronized, TracedModel


def detect(save_img=False):
    source, weights1, weights2, view_img, save_txt, imgsz, trace = opt.source, opt.weights1, opt.weights2, opt.view_img, opt.save_txt, opt.img_size, not opt.no_trace
    save_img = not opt.nosave and not source.endswith('.txt')  # save inference images
    webcam = source.isnumeric() or source.endswith('.txt') or source.lower().startswith(
        ('rtsp://', 'rtmp://', 'http://', 'https://'))

    # Directories
    save_dir = Path(increment_path(Path(opt.project) / opt.name, exist_ok=opt.exist_ok))  # increment run
    (save_dir / 'labels' if save_txt else save_dir).mkdir(parents=True, exist_ok=True)  # make dir

    # Initialize
    set_logging()
    device = select_device(opt.device)
    half = device.type != 'cpu'  # half precision only supported on CUDA

    # Load models
    model1 = attempt_load(weights1, map_location=device)  # load FP32 model1 (person detection)
    model2 = attempt_load(weights2, map_location=device)  # load FP32 model2 (name tag detection)
    stride1 = int(model1.stride.max())  # model1 stride
    stride2 = int(model2.stride.max())  # model2 stride
    imgsz = check_img_size(imgsz, s=max(stride1, stride2))  # check img_size

    if trace:
        model1 = TracedModel(model1, device, opt.img_size)
        model2 = TracedModel(model2, device, opt.img_size)

    if half:
        model1.half()  # to FP16
        model2.half()  # to FP16

    # Set Dataloader
    vid_path, vid_writer = None, None
    if webcam:
        view_img = check_imshow()
        cudnn.benchmark = True  # set True to speed up constant image size inference
        dataset = LoadStreams(source, img_size=imgsz, stride=max(stride1, stride2))
    else:
        dataset = LoadImages(source, img_size=imgsz, stride=max(stride1, stride2))

    # Get names and colors
    names1 = model1.module.names if hasattr(model1, 'module') else model1.names
    names2 = model2.module.names if hasattr(model2, 'module') else model2.names
    colors1 = [[random.randint(0, 255) for _ in range(3)] for _ in names1]
    colors2 = [[random.randint(0, 255) for _ in range(3)] for _ in names2]

    # Run inference
    if device.type != 'cpu':
        model1(torch.zeros(1, 3, imgsz, imgsz).to(device).type_as(next(model1.parameters())))  # run once
        model2(torch.zeros(1, 3, imgsz, imgsz).to(device).type_as(next(model2.parameters())))  # run once
    old_img_w = old_img_h = imgsz
    old_img_b = 1

    t0 = time.time()
    for path, img, im0s, vid_cap in dataset:
        img = torch.from_numpy(img).to(device)
        img = img.half() if half else img.float()  # uint8 to fp16/32
        img /= 255.0  # 0 - 255 to 0.0 - 1.0
        if img.ndimension() == 3:
            img = img.unsqueeze(0)

        # Warmup
        if device.type != 'cpu' and (old_img_b != img.shape[0] or old_img_h != img.shape[2] or old_img_w != img.shape[3]):
            old_img_b = img.shape[0]
            old_img_h = img.shape[2]
            old_img_w = img.shape[3]
            for i in range(3):
                model1(img, augment=opt.augment)[0]
                model2(img, augment=opt.augment)[0]

        # Inference
        t1 = time_synchronized()
        with torch.no_grad():   # Calculating gradients would cause a GPU memory leak
            pred1 = model1(img, augment=opt.augment)[0]  # person detection
            pred2 = model2(img, augment=opt.augment)[0]  # name tag detection
        t2 = time_synchronized()

        # Apply NMS
        pred1 = non_max_suppression(pred1, opt.conf_thres, opt.iou_thres, classes=opt.classes, agnostic=opt.agnostic_nms)
        pred2 = non_max_suppression(pred2, opt.conf_thres, opt.iou_thres, classes=opt.classes, agnostic=opt.agnostic_nms)
        t3 = time_synchronized()

        # Process detections
        for i, (det1, det2) in enumerate(zip(pred1, pred2)):  # detections per image
            if webcam:  # batch_size >= 1
                p, s, im0, frame = path[i], '%g: ' % i, im0s[i].copy(), dataset.count
            else:
                p, s, im0, frame = path, '', im0s, getattr(dataset, 'frame', 0)

            p = Path(p)  # to Path
            save_path = str(save_dir / p.name)  # img.jpg
            txt_path = str(save_dir / 'labels' / p.stem) + ('' if dataset.mode == 'image' else f'_{frame}')  # img.txt
            gn = torch.tensor(im0.shape)[[1, 0, 1, 0]]  # normalization gain whwh
            if len(det1) and len(det2):
                # Rescale boxes from img_size to im0 size
                det1[:, :4] = scale_coords(img.shape[2:], det1[:, :4], im0.shape).round()
                det2[:, :4] = scale_coords(img.shape[2:], det2[:, :4], im0.shape).round()

                # Collect xy coordinates of person with name tag
                person_with_name_tag_coords = []
                for *xyxy_person, _, _ in det1:
                    for *xyxy_name_tag, _, _ in det2:
                        if xyxy_person[0] < xyxy_name_tag[0] < xyxy_person[2] and xyxy_person[1] < xyxy_name_tag[1] < xyxy_person[3]:
                            person_with_name_tag_coords.append((xyxy_person, xyxy_name_tag))  # store both person and name tag coordinates

                # Draw bounding boxes and write xy coordinates
                for xyxy_person, xyxy_name_tag in person_with_name_tag_coords:
                    # Add person detection bounding box to image
                    label = 'Person'
                    plot_one_box(xyxy_person, im0, label=label, color=(0, 255, 0), line_thickness=1)

                    # Add name tag detection bounding box to image
                    plot_one_box(xyxy_name_tag, im0, label=label, color=(255, 0, 0), line_thickness=1)

                    # # Write xy coordinates
                    # text = f'XY Person: ({int(xyxy_person[0])}, {int(xyxy_person[1])})'
                    # cv2.putText(im0, text, (20, im0.shape[0] - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                    # text = f'XY Name Tag: ({int(xyxy_name_tag[0])}, {int(xyxy_name_tag[1])})'
                    # cv2.putText(im0, text, (20, im0.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)

                    # Write xy coordinates including x_min, y_min, x_max, y_max
                    text = f'XY Person: ({int(xyxy_person[0])}, {int(xyxy_person[1])}, {int(xyxy_person[2])}, {int(xyxy_person[3])})'
                    cv2.putText(im0, text, (20, im0.shape[0] - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                    
                    text = f'XY Name Tag: ({int(xyxy_name_tag[0])}, {int(xyxy_name_tag[1])}, {int(xyxy_name_tag[2])}, {int(xyxy_name_tag[3])})'
                    cv2.putText(im0, text, (20, im0.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)


            # Print time (inference + NMS)
            print(f'{s}Done. ({(1E3 * (t2 - t1)):.1f}ms) Inference, ({(1E3 * (t3 - t2)):.1f}ms) NMS')

            # Stream results
            if view_img:
                cv2.imshow(str(p), im0)
                cv2.waitKey(1)  # 1 millisecond

            # Save results (image with detections)
            if save_img:
                if dataset.mode == 'image':
                    cv2.imwrite(save_path, im0)
                    print(f" The image with the result is saved in: {save_path}")
                else:  # 'video' or 'stream'
                    if vid_path != save_path:  # new video
                        vid_path = save_path
                        if isinstance(vid_writer, cv2.VideoWriter):
                            vid_writer.release()  # release previous video writer
                        if vid_cap:  # video
                            fps = vid_cap.get(cv2.CAP_PROP_FPS)
                            w = int(vid_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                            h = int(vid_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                        else:  # stream
                            fps, w, h = 30, im0.shape[1], im0.shape[0]
                            save_path += '.mp4'
                        vid_writer = cv2.VideoWriter(save_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))
                    vid_writer.write(im0)

    if save_txt or save_img:
        s = f"\n{len(list(save_dir.glob('labels/*.txt')))} labels saved to {save_dir / 'labels'}" if save_txt else ''
        #print(f"Results saved to {save_dir}{s}")

    print(f'Done. ({time.time() - t0:.3f}s)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--weights1', type=str, default='yolov7_1.pt', help='model1.pt path (person detection)')
    parser.add_argument('--weights2', type=str, default='yolov7_2.pt', help='model2.pt path (name tag detection)')
    parser.add_argument('--source', type=str, default='inference/images', help='source')  # file/folder, 0 for webcam
    parser.add_argument('--img-size', type=int, default=640, help='inference size (pixels)')
    parser.add_argument('--conf-thres', type=float, default=0.25, help='object confidence threshold')
    parser.add_argument('--iou-thres', type=float, default=0.45, help='IOU threshold for NMS')
    parser.add_argument('--device', default='', help='cuda device, i.e. 0 or 0,1,2,3 or cpu')
    parser.add_argument('--view-img', action='store_true', help='display results')
    parser.add_argument('--save-txt', action='store_true', help='save results to *.txt')
    parser.add_argument('--save-conf', action='store_true', help='save confidences in --save-txt labels')
    parser.add_argument('--nosave', action='store_true', help='do not save images/videos')
    parser.add_argument('--classes', nargs='+', type=int, help='filter by class: --class 0, or --class 0 2 3')
    parser.add_argument('--agnostic-nms', action='store_true', help='class-agnostic NMS')
    parser.add_argument('--augment', action='store_true', help='augmented inference')
    parser.add_argument('--update', action='store_true', help='update all models')
    parser.add_argument('--project', default='runs/detect', help='save results to project/name')
    parser.add_argument('--name', default='exp', help='save results to project/name')
    parser.add_argument('--exist-ok', action='store_true', help='existing project/name ok, do not increment')
    parser.add_argument('--no-trace', action='store_true', help='don`t trace model')
    opt = parser.parse_args()
    print(opt)
    #check_requirements(exclude=('pycocotools', 'thop'))

    with torch.no_grad():
        if opt.update:  # update all models (to fix SourceChangeWarning)
            for opt.weights1, opt.weights2 in [('yolov7_1.pt', 'yolov7_2.pt')]:
                detect()
                strip_optimizer(opt.weights1)
                strip_optimizer(opt.weights2)
        else:
            detect()
