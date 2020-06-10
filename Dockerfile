FROM ctmanjak/codedu_base:docker

WORKDIR /codedu
ADD ./requirements.txt   /codedu/requirements.txt
RUN pip3 install -r requirements.txt

ADD ./terminal.py   /codedu/terminal.py