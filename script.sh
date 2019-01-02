#!/bin/bash


## input 1 is the file we're operating on
## input 2 could be number of desired jobs instead of counter?


## USER CONFIG VARS ##

# parse mediainfo output for frame count, select number field, and 
# strip whitespace
frameCount=`mediainfo --fullscan $1 | \
  grep -m 1 'Frame count' | \
  cut -d ':' -f 2 | \
  sed "s/ //"`

  
#new framecount code - commented out for now

#frameCount=`mediainfo --fullscan --Output=JSON incred2.mkv | jq '.media.track[0].FrameCount''

#echo framecount "$framecount"

# jobnum controls how many jobs we're making, we should make this an input
jobnum=6

#crop detection code cribbed elsewhere
cropdetect="$(ffmpeg -ss 900 -i $1 -t 1 -vf "cropdetect=24:16:0" -preset ultrafast -f null - 2>&1 | awk '/crop/ { print $NF }' | tail -1)"

## USER CONFIG VARS ##
buffer=100 #frames on either side of job, should take this as input

# jobsize is number of frames per job + 1 to round, better to go off the end than come up short
jobsize=$(((( frameCount / jobnum )) +1 ))

#echo jobsize $jobsize 

counter=0
seek=0 #first job only to begin at 0
chunkstart=0 #first job only to start at 0
chunkend=$((jobsize -1))
#echo chunkstart $chunkstart
frames=$(( jobsize + buffer )) #FIRST job gets buffer on one side only
#echo frames $frames
#first job
	echo "ffmpeg -hide_banner -i "$1" -filter:v "\""$cropdetect"\"" -strict -1 -f yuv4mpegpipe - | x265 - --no-open-gop --seek $seek --frames $frames --chunk-start $chunkstart --chunk-end $chunkend --colorprim bt709 --transfer bt709 --colormatrix bt709 --crf=20 --fps 24000/1001 --min-keyint 24 --keyint 240 --sar 1:1 --preset slow --ctu 16 --y4m --pools "+" -o chunky"$counter".265"

	counter=1 #we did a job outside the loop
	
chunkstart=$(( buffer )) #fixing chunkstart
chunkend=$((jobsize + chunkstart)) # eliminating the buffer from the start

seek=$((seek + frames - buffer - buffer))

#echo seek before loop $seek

while [ $counter -lt $jobnum ]; do

	frames=$(( jobsize + buffer + buffer)) #jobs in the loop arte buffered on both sides
#	echo frames in loop top $frames
 
	chunkend=$(((( $chunkstart + $jobsize )) - 1))

	echo "ffmpeg -hide_banner -i "$1" -filter:v "\""$cropdetect"\"" -strict -1 -f yuv4mpegpipe - | x265 - --no-open-gop --seek $seek --frames $frames --chunk-start $chunkstart --chunk-end $chunkend --colorprim bt709 --transfer bt709 --colormatrix bt709 --crf=20 --fps 24000/1001 --min-keyint 24 --keyint 240 --sar 1:1 --preset slow --ctu 16 --y4m --pools "+" -o chunky"$counter".265"
#	echo seek $seek
	
  # seek should be $buffer frames less than the last ending streamed frame
  seek=$(( seek + frames - buffer - buffer))
 

  
   ((counter++))


done

