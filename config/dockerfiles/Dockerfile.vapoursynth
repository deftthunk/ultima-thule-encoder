FROM alpine

ENV WORK_PATH='/opt'
WORKDIR ${WORK_PATH}
VOLUME [${WORK_PATH}]
# Common Env
# RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.ustc.edu.cn/g' /etc/apk/repositories && \ 
RUN apk update && apk upgrade && \
    apk --no-cache add \
    build-base unzip gcc g++ cmake curl linux-headers bash \
    git mercurial nasm yasm libtool pkgconfig autoconf automake coreutils python3-dev  \
    fftw-dev libpng-dev libsndfile-dev xvidcore-dev libbluray-dev \
    zlib-dev opencl-icd-loader-dev opencl-headers \
    boost-filesystem boost-system && \
    pip3 install cython && \
    sed -i 's/ash/bash/g' /etc/passwd && \
    echo "export PYTHONPATH=/usr/local/lib/python3.6/site-packages" >> /etc/profile
ENTRYPOINT ["/bin/bash","-l"]
# # zimg
RUN git clone https://github.com/sekrit-twc/zimg.git && \
    cd zimg && \
    ./autogen.sh && bash -c ./configure && \
    make -j$(nproc) && make install && \
    cd .. && rm -rf zimg
# Vapoursynth
RUN git clone https://github.com/vapoursynth/vapoursynth.git && \
    cd vapoursynth && \
    ./autogen.sh && bash -c ./configure && \
    make -j$(nproc) && make install && \
    cd .. && rm -rf vapoursynth
# Plugins  
RUN git clone https://github.com/darealshinji/vapoursynth-plugins.git && \
    cd vapoursynth-plugins && \
    ./autogen.sh && bash -c ./configure && \
    make -j$(nproc) && make install && \
    cd .. && rm -rf vapoursynth-plugins
# build-X265
RUN hg clone https://bitbucket.org/multicoreware/x265 && \
    cd x265/build/linux && \
    # x265_8bit
    cmake -G "Unix Makefiles" ../../source -DENABLE_SHARED=OFF -DENABLE_CLI=OFF && make -j$(nproc) && cp libx265.a /usr/local/lib/libx265_main.a && \
    cmake -G "Unix Makefiles" ../../source -DENABLE_SHARED=ON -DENABLE_CLI=OFF && make -j$(nproc) && cp libx265.so /usr/local/lib/libx265_main.so && \
    cmake -G "Unix Makefiles" ../../source -DENABLE_SHARED=OFF -DENABLE_CLI=ON && make -j$(nproc) && cp x265 /usr/local/bin/x265 && \
    # x265_10bit
    cmake -G "Unix Makefiles" ../../source -DENABLE_SHARED=OFF -DENABLE_CLI=OFF -DHIGH_BIT_DEPTH=ON -DMAIN12=OFF && make -j$(nproc) && cp libx265.a /usr/local/lib/libx265_main10.a && \
    cmake -G "Unix Makefiles" ../../source -DENABLE_SHARED=ON -DENABLE_CLI=OFF -DHIGH_BIT_DEPTH=ON -DMAIN12=OFF && make -j$(nproc) && cp libx265.so /usr/local/lib/libx265_main10.so && \
    cmake -G "Unix Makefiles" ../../source -DENABLE_SHARED=OFF -DENABLE_CLI=ON -DHIGH_BIT_DEPTH=ON -DMAIN12=OFF && make -j$(nproc) && cp x265 /usr/local/bin/x265_10bit && \
    # x265_12bit
    # cmake -G "Unix Makefiles" ../../source -DENABLE_SHARED=OFF -DENABLE_CLI=OFF -DHIGH_BIT_DEPTH=ON -DMAIN12=ON && make -j$(nproc) && cp libx265.a /usr/local/lib/libx265_main12.a && \
    # cmake -G "Unix Makefiles" ../../source -DENABLE_SHARED=ON -DENABLE_CLI=OFF -DHIGH_BIT_DEPTH=ON -DMAIN12=ON && make -j$(nproc) && cp libx265.so /usr/local/lib/libx265_main12.so && \
    # cmake -G "Unix Makefiles" ../../source -DENABLE_SHARED=OFF -DENABLE_CLI=ON -DHIGH_BIT_DEPTH=ON -DMAIN12=ON && make -j$(nproc) && cp x265 /usr/local/bin/x265_12bit && \
    rm -rf /opt/x265
# ffmpeg
RUN apk --no-cache add ffmpeg && pip3 install vapoursynth
