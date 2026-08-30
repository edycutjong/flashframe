#!/usr/bin/env bash
set -e

mkdir -p assets
cd assets

# Clip 1
ffmpeg -y -f lavfi -i "color=c=black:s=1280x720:r=25:d=60" \
  -vf "geq=lum='60+120*(0.5+0.5*sin(2*PI*0.5*T))':cb=128:cr=128" \
  -c:v libx264 -preset veryslow -crf 12 -pix_fmt yuv420p \
  -x264-params keyint=25:scenecut=0 control_clean.mp4

# Clip 2
ffmpeg -y -f lavfi -i "color=c=black:s=1280x720:r=25:d=29.56" \
  -vf "geq=lum='60+120*(0.5+0.5*sin(2*PI*0.5*T))':cb=128:cr=128" \
  -c:v libx264 -preset veryslow -crf 12 -pix_fmt yuv420p seg_a.mp4

ffmpeg -y -f lavfi -i "color=c=black:s=1280x720:r=25:d=0.88" \
  -vf "geq=lum='if(lt(mod(floor(T*25),4),2),40,200)':cb=128:cr=128" \
  -c:v libx264 -preset veryslow -crf 12 -pix_fmt yuv420p seg_b.mp4

ffmpeg -y -f lavfi -i "color=c=black:s=1280x720:r=25:d=29.56" \
  -vf "geq=lum='60+120*(0.5+0.5*sin(2*PI*0.5*T))':cb=128:cr=128" \
  -c:v libx264 -preset veryslow -crf 12 -pix_fmt yuv420p seg_c.mp4

cat <<EOF > segments.txt
file 'seg_a.mp4'
file 'seg_b.mp4'
file 'seg_c.mp4'
EOF

ffmpeg -y -f concat -safe 0 -i segments.txt -c copy hard_fail_strobe.mp4
rm seg_a.mp4 seg_b.mp4 seg_c.mp4 segments.txt

# Clip 3
ffmpeg -y -f lavfi -i "color=c=black:s=1280x720:r=25:d=60" \
  -vf "geq=lum='if(between(floor(T*25),1000,1074)*lt(X,426)*lt(Y,240), if(lt(mod(floor(T*25),10),5),40,200), 60+120*(0.5+0.5*sin(2*PI*0.5*T)))':cb=128:cr=128" \
  -c:v libx264 -preset veryslow -crf 12 -pix_fmt yuv420p borderline_screen_area.mp4
