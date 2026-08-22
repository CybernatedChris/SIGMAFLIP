<h1 align="center">
  <img src="https://github.com/CybernatedChris/SIGMAFLIP/blob/main/sf/assets/img/sigma.png">
  SIGMAFLIP
  <img src="https://github.com/CybernatedChris/SIGMAFLIP/blob/main/sf/assets/img/sigma.png">
</h1>

A FOSS Python alternative to the traditional SignaPic DSi, with video frame splitting and other rich features.

> [!WARNING]
> While this tool only signs pictures/split video frames into readable JPEGs, I am NOT liable on what you use this tool for or what you convert. Please proceed at your own risk!

## What it is and what it's not:
✔ Another JPEG signer for the DSi, focused primarily for Flipnote Studio. <br>
✔ An open source SignaPic DSi alternative <br>
❌ A .ppm decoder <br>

## Where did the name originated?
The name "SIGMAFLIP" is a cheesy pun named after SignaPic DSi. SIGMAFLIP is preferred to be in all caps just like with MF DOOM. We all know SignaPic DSi is mainly used for Flipnote Studio, hence where FLIP came from, and the tool has more flexible features than SignaPic already has, so it's the ultimate sigma JPEG signer out there 💪🗿🔥 

## Why did you make SIGMAFLIP?
By pure accident. The story is, I thought that I can use this tool to only split the frames of a video with FFmpeg and math, then port all of them to SignaPic until I thought about the dsi_jpeg_signer_tool I found earlier. Yes SignaPic does work, but I feel like there should be some newer features that would make flipnoting better. SignaPic only allows JPEGs, SIGMAFLIP accepts all known types of image formats.

## How to use this?
Choose the dropdown mode between Video Frames or Singular Image. For Video Frames, choose any video supported by SIGMAFLIP.

### Importing images onto a 3DS 
The DCIM structure for the 3DS is very much the same as the DSi. Unlike the DSi, **you don't need to delete a pit.bin file. Use the 3DS Camera instead.**

## SignaPic DSi and SIGMAFLIP Differences:
|       | SignaPic DSi | SIGMAFLIP |
|-------|--------|---------|
| Platform | Windows Only* | Cross Platform |
| Video support? | ❌ | ✔ |
| File Format | JPEG only   | PNG, JPEG/JPG, WEBP, BMP (Singular Image) MP4 AVI MOV MKV GIF (Video Frames)|
| Last Activity | around 2019 to 2022 | 2026 and beyond (until I say so) |
| Other known features | Resizing, widecut, and proportionate images, DPI optimization | More resizing options such as title layout, additional filters, black/white/custom backgrounds, DCIM tree or Part_X tree exporting layouts, 3DS support, pit.bin deletion/detection confirmation|

*Works natively under Wine

## What you need (for manually executing):
- Python 3.14+ recommended
- FFmpeg

### Manual execution
#### By manually running, be sure to keep the assets folder in the same directory as the main.py script, or else you will encounter errors. <br>

Clone the repo or download the source code from the [releases](https://github.com/CybernatedChris/SIGMAFLIP/releases): <br>
```git clone https://github.com/CybernatedChris/SIGMAFLIP.git```

Windows steps: <br>
cd into the SIGMAFLIP's folder, then run: <br>
```pip install -r requirements.txt``` <br>
and then <br>
```py main.py```


Mac/Linux steps (or Windows optionally): <br>
It's best to make a virtual environment, open a terminal to where your venv should be saved:
```
python -m venv sfvenv

source sfvenv/bin/activate

cd SIGMAFLIP

pip install -r requirements.txt

python main.py
```

## Screenshots
<img width="502" height="632" alt="videoselector" src="https://github.com/user-attachments/assets/57c2a652-aa81-4451-abdf-3f9adda5f250" />
<img width="502" height="632" alt="videoselectorgrid" src="https://github.com/user-attachments/assets/dd5eb2d4-c299-4fd0-b2bd-991d32051f56" />
<img width="502" height="632" alt="imageselector" src="https://github.com/user-attachments/assets/083ce05a-21e2-462d-8f20-fbd0fa9c3be9" />
<img width="502" height="632" alt="imgselectorbulkpreview" src="https://github.com/user-attachments/assets/6b4f1c35-c63e-4b69-9e38-5d33df13be1d" />

As of v1

## Limitations:
The Flipnote Memory Saver filter is experimental. When you use the filter in Flipnote Studio, you must move the cursor at this spot: <br />
<img width="562" height="844" alt="memsavelocator" src="https://github.com/user-attachments/assets/677546e2-63b8-4a66-8a07-afdfe8e98dd8" /> <br />
Your image may appear darker/lighter than expected, use the contrast settings.

## Special Thanks
- cimo95: inspiration of the SIGMAFLIP [(see SignaPic DSi here)](https://gbatemp.net/threads/signapic-dsi-simple-step-to-import-any-jpg-images-into-nintendo-dsi.552288/)
- MrNbaYoh: the source of how to sign pictures into the DSi https://github.com/MrNbaYoh/dsi_jpeg_signature_tool

####

## License
Due to the nature of this code being slopped together (except for the art) and some sprinkled in shitpost humor, this project is licensed under the WTFPL license, thus the reason why I closed this project from future issues or pull requests. Here's what you can do: fork the project, vibe code even more on it, or just do whatever the fuck you want!

<a href="http://www.wtfpl.net/"><img
       src="http://www.wtfpl.net/wp-content/uploads/2012/12/wtfpl-badge-4.png"
       width="80" height="15" alt="WTFPL" /></a>

       
**(SIGMAFLIP is not in relation with cimo95, MrNbaYoh, Nintendo, and Flipnote Studio. All sounds and Flipnote Studio belong to Nintendo.)**
