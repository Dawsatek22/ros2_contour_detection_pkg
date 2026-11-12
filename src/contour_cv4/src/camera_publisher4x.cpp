
// this is a node to publish camera images as ros2 msgs with opencv version 4.6 currently
#include "rclcpp/rclcpp.hpp" // c++ client library

#include <sensor_msgs/msg/image.hpp> // msg type
#include <cv_bridge/cv_bridge.hpp> // needed as a hpp file  for this current version of opencv
#include <opencv2/opencv.hpp> // for opencv functionality

// creates class
class Camera_publishernode : public  rclcpp::Node
{
public:
Camera_publishernode() :
Node("camera_publsiher"), cam(-1) // default value
{
// declare and get the camera index parameter
this->declare_parameter<int>("camera_index",0);
int camera_index = this->get_parameter("camera_index").as_int();

// open the camera
cam.open(camera_index);

// check if camera is opened
if (!cam.isOpened()) {
    
    RCLCPP_ERROR(this->get_logger(), "Failed to open camera");
    throw std::runtime_error("Camera not available");
        

}
// create publisher below
camera_publisher_ = this->create_publisher<sensor_msgs::msg::Image>("camera/image",10);
// creates timer below
camera_timer_ = this->create_wall_timer(std::chrono::milliseconds(30),
std::bind(&Camera_publishernode::publish_camera,this));

RCLCPP_INFO(this->get_logger(),"Starting camera publisher in c++... ");

}

private:

cv::VideoCapture cam;

rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr camera_publisher_;
rclcpp::TimerBase::SharedPtr camera_timer_;

void publish_camera() {

 // creates camera object
cv::Mat frame;
cam >> frame; // captures camera frame
if (frame.empty()) {
RCLCPP_WARN(this->get_logger(),"Empty frame captured");
return;

}
sensor_msgs::msg::Image::SharedPtr cam_msg;
cam_msg = cv_bridge::CvImage(std_msgs::msg::Header(),"bgr8",frame).toImageMsg();
// publish the the msg
camera_publisher_->publish(*cam_msg);


}

};


int main(int argc, char **argv) {

rclcpp::init(argc,argv);
auto node = std::make_shared<Camera_publishernode>();
rclcpp::spin(node);
rclcpp::shutdown();
return 0;
}