# MM404
Meeting session process

## Setup
```
sudo docker build . -t mm404
```

## Running
```
sudo docker run -v $(pwd):/usr/mm/mm404 mm404 python main.py
```