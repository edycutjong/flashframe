#!/bin/bash
# Generate filtergraph
echo "split=10[full][t1][t2][t3][t4][t5][t6][t7][t8][t9];" > filter.txt
echo "[full]signalstats,metadata=print:file=stats_0.txt[out0];" >> filter.txt
echo "[t1]crop=iw/3:ih/3:0:0,signalstats,metadata=print:file=stats_1.txt[out1];" >> filter.txt
echo "[t2]crop=iw/3:ih/3:iw/3:0,signalstats,metadata=print:file=stats_2.txt[out2];" >> filter.txt
echo "[t3]crop=iw/3:ih/3:2*iw/3:0,signalstats,metadata=print:file=stats_3.txt[out3];" >> filter.txt
echo "[t4]crop=iw/3:ih/3:0:ih/3,signalstats,metadata=print:file=stats_4.txt[out4];" >> filter.txt
echo "[t5]crop=iw/3:ih/3:iw/3:ih/3,signalstats,metadata=print:file=stats_5.txt[out5];" >> filter.txt
echo "[t6]crop=iw/3:ih/3:2*iw/3:ih/3,signalstats,metadata=print:file=stats_6.txt[out6];" >> filter.txt
echo "[t7]crop=iw/3:ih/3:0:2*ih/3,signalstats,metadata=print:file=stats_7.txt[out7];" >> filter.txt
echo "[t8]crop=iw/3:ih/3:iw/3:2*ih/3,signalstats,metadata=print:file=stats_8.txt[out8];" >> filter.txt
echo "[t9]crop=iw/3:ih/3:2*iw/3:2*ih/3,signalstats,metadata=print:file=stats_9.txt[out9]" >> filter.txt

rm -f stats_*.txt
ffmpeg -i test_clip.mp4 -filter_complex_script filter.txt -map "[out0]" -map "[out1]" -map "[out2]" -map "[out3]" -map "[out4]" -map "[out5]" -map "[out6]" -map "[out7]" -map "[out8]" -map "[out9]" -f null -
