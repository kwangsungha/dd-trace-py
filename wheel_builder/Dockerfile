FROM quay.io/pypa/manylinux2014_x86_64
WORKDIR /app

# Install necessary packages
RUN yum install -y \
        git \
        gcc \
        gcc-c++ \
        make \
        automake \
        doxygen \
        gettext-devel \
        po4a \
        autoconf \
        curl \
        vim \
        wget \
        patchelf \
        xz-devel \
        libunwind-devel

# Python
ENV PYTHON /opt/python/cp311-cp311/bin/python
RUN ln -sf ${PYTHON} /usr/bin/python
RUN ${PYTHON} -m pip install --upgrade wheel auditwheel build cython

COPY ./*.a /usr/lib/

## libunwind
#RUN git clone https://github.com/libunwind/libunwind.git && \
#    cd libunwind && \
#    autoreconf -i && \
#    ./configure --enable-static && \
#    make && \
#    find . -name '*.a' -exec mv {} /usr/l
#
## xz
#RUN git clone https://github.com/tukaani-project/xz && \
#    cd xz && \
#    git fetch --all --tags && \
#    git checkout v5.4.4 && \
#    ./autogen.sh && \
#    ./configure --disable-shared && \
#    make && \
#    make install

VOLUME /tmp/wheels
VOLUME /app/dd-trace-py
VOLUME /app/cpython

COPY ./run.sh /app/

ENTRYPOINT ["/app/run.sh"]
