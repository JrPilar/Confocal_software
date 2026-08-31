#!/usr/bin/env python

import socket
import matplotlib.pyplot as plt
import numpy as np

# open the device on the localhost, port 9999
t = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
t.connect(('127.0.0.1', 9999))

# read the server response
data = t.recv(8192)
print(data.decode('utf8'))

# set the measurement times sweep from 50 to 500 ms
integration_times = np.arange(50, 550, 50)
counts = np.zeros(len(integration_times))
iteration = 0
for i in integration_times: 
    # start an intensity measurement for i ms and read the data
    command = bytes("I," + str(i) + '\n', "utf8")
    t.send(command)
    
    data = t.recv(8192) # get the data
    datastr = data.decode('utf8') # decode into a string
    datasplit = datastr.split("\n") # split the lines
    for j in datasplit: 
        # get the counts for each column and add them together
        readdata = j.split(",")
        counts[iteration] += int(readdata[1])
    
    iteration += 1

# make a plot of the integration time versus the count rate
plt.plot(integration_times, counts) # this should be a pretty linear line
plt.title('Count rate vs. integration time')
plt.xlabel('Integration time [ms]')
plt.ylabel('Counts [#]')
plt.grid(True)
plt.show()

# close communication channel
t.close()