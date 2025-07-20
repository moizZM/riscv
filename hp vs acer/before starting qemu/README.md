# before starting qemu 


````py

sudo ip tuntap add dev tap0 mode tap user $USER
sudo ip link set tap0 up

```


````py

sudo iptables -t nat -A PREROUTING -p udp -d 192.168.1.25 --dport 12345 -j DNAT --to-destination 192.168.100.10:12345
sudo iptables -t nat -A POSTROUTING -p udp -d 192.168.100.10 --dport 12345 -j MASQUERADE

```



````py

sudo ip addr add 192.168.100.1/24 dev tap0

```

