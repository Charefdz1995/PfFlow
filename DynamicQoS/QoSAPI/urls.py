from django.urls import path 
from .views import *

urlpatterns = [
    path('add',AddDevice.as_view(), name = "add-device"),
    path('topology',TopologyByName.as_view(), name = "topology-by-name"),
    path('topologies',TopologyList.as_view(), name = "topologies-list"),
    path('addtopogy', AddTopology.as_view(), name = "add-topology"), 
]
app_name = 'QoSAPI'